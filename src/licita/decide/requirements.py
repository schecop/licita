"""Solvency the company plainly fails: decided by code from cited fields, before any model.

Only fields whose quote was verified can produce a code no-go (a no-go must cite its clause).
Each check answers pass, fail or open; "open" goes to the decision model with the field.

Solvency comes as the pliego states it (ADR-0003): a list of criteria, each of a kind, with a fixed
amount, an amount relative to the contract, or no amount, and whether any one of them is enough
("o bien ...") or all are required. The first nine real pliegos used all of these: "≥ 90.000
euros", "1,5 veces el valor anual medio", "igual o superior al valor estimado", a professional
liability insurance of 75.000 €, a positive net worth, a list of works with no amount.

- A relative amount is computed from the feed (valor estimado, presupuesto: checked against the
  PLACSP pages in the golden set). The annual value is the valor estimado over the maximum
  duration with extensions (`max_duration_months`), as the valor estimado itself includes them.
- Economic: turnover, insurance and net worth are compared with the profile; anything else is
  open. Technical: similar services fail only if even the company's 3-year total is below the
  amount (an annual amount in the best year may still be met); every other kind is open.
- Cumulative criteria: one computable fail fails; all computable passes and nothing open, pass.
  Alternatives ("o bien"): one computable pass passes; fail only if every one is computable and
  fails. A false code no-go is the worst error here, so anything unclear is open.
- ENS (Esquema Nacional de Seguridad) is compared by category, BÁSICA < MEDIA < ALTA, against the
  profile's `ens_level`; open if the pliego names no category. It is checked before the number
  match, because "ENS (RD 311/2022)" carries a number that is not a certification's.
- Other certifications with a number (ISO 9001, ISO/IEC 27001) are matched by that number; without
  one they are open.
- Classification required: open. For services it is usually an alternative to solvency, and
  whether it is here is wording, not a number.

`unestablished` is the other half: every pliego states (or explicitly waives) its economic and
technical solvency, so if either did not come out usable (not found, uncited, a quote that did
not check, a cut answer), nothing was compared and the tender goes to review by code. Without it
a model's "go" with high confidence would be auto-accepted over solvency nobody checked.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from licita.extract.citations import Citation, FieldResult
from licita.placsp.feed import Tender
from licita.profile import CompanyProfile, Solvency
from licita.retrieval.text import fold

Verdict = Literal["pass", "fail", "open"]
SOLVENCY_FIELDS = ("economic_solvency", "technical_solvency")
ENS_LEVELS = {"basica": 1, "basico": 1, "media": 2, "medio": 2, "alta": 3, "alto": 3}


@dataclass(frozen=True)
class Requirement:
    name: str
    verdict: Verdict
    reason: str
    citation: Citation | None


def _eur(v: Decimal) -> str:
    return f"€{v:,.0f}"


def _dec(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _months(fields: Mapping[str, FieldResult]) -> Decimal | None:
    f = fields.get("max_duration_months")
    m = _dec(f.value) if f and f.check == "verified" else None
    return m if m and m > 0 else None


def threshold(
    c: Mapping[str, Any], tender: Tender | None, months: Decimal | None
) -> tuple[Decimal | None, str]:
    """The amount a criterion asks for, and how it was obtained; None if it cannot be computed."""
    if (amount := _dec(c.get("amount_eur"))) is not None:
        return amount, _eur(amount)
    multiple, base = _dec(c.get("multiple")), c.get("base")
    if multiple is None or base is None:
        return None, "no amount"
    value = None
    if tender is not None and base == "estimated_value":
        value = tender.estimated_value_eur
    elif tender is not None and base == "budget":
        value = tender.budget_eur
    elif tender is not None and base == "annual_value" and tender.estimated_value_eur:
        if months is None:
            return None, f"{multiple} x annual value (duration with extensions unknown)"
        value = tender.estimated_value_eur * 12 / months
    if value is None:
        return None, f"{multiple} x {base} (unknown)"
    need = (multiple * value).quantize(Decimal("0.01"))
    return need, f"{_eur(need)} ({multiple} x {base} {_eur(value)})"


def _criterion(
    group: str, c: Mapping[str, Any], s: Solvency, tender: Tender | None, months: Decimal | None
) -> tuple[Verdict, str]:
    kind = str(c.get("kind") or "other")
    need, how = threshold(c, tender, months)
    have: Decimal | None = None
    if group == "economic":
        if kind == "net_worth" and need is None and c.get("multiple") is None:
            need, how = Decimal(0), "positive"
        have = {
            "turnover": s.annual_turnover_eur,
            "insurance": s.professional_liability_insurance_eur,
            "net_worth": s.net_worth_eur,
        }.get(kind)
        if kind in ("turnover", "insurance", "net_worth") and need is not None and have is not None:
            ok = have > need if kind == "net_worth" else have >= need
            return ("pass" if ok else "fail"), f"{kind} {how}: company {_eur(have)}"
        return "open", f"{kind} {how}"
    if kind == "similar_services" and need is not None:
        have = s.similar_contracts_last_3y_eur
        if have < need:
            return "fail", f"similar services {how}: company's 3-year total {_eur(have)}"
        return "open", f"similar services {how}: company's 3-year total {_eur(have)}, one year?"
    return "open", f"{kind} {how}"


def _combine(verdicts: Sequence[Verdict], any_of: bool) -> Verdict:
    if not verdicts:
        return "open"
    if any_of:
        if "pass" in verdicts:
            return "pass"
        return "fail" if all(v == "fail" for v in verdicts) else "open"
    if "fail" in verdicts:
        return "fail"
    return "pass" if all(v == "pass" for v in verdicts) else "open"


def _solvency(
    group: str, f: FieldResult, s: Solvency, tender: Tender | None, months: Decimal | None
) -> Requirement:
    name = f"{group}_solvency"
    if f.check == "explicit_none":
        return Requirement(name, "pass", "the pliego requires none", f.citation)
    value = f.value if isinstance(f.value, Mapping) else {}
    criteria = [c for c in value.get("criteria") or [] if isinstance(c, Mapping)]
    any_of = bool(value.get("any_of"))
    results = [_criterion(group, c, s, tender, months) for c in criteria]
    verdict = _combine([v for v, _ in results], any_of)
    joiner = " OR " if any_of else "; "
    reason = joiner.join(f"{r} [{v}]" for v, r in results) or "no criteria listed"
    return Requirement(name, verdict, reason, f.citation)


def _numbers(cert: str) -> set[str]:
    return set(re.findall(r"\d{4,5}", cert))


def _is_ens(cert: str) -> bool:
    folded = fold(cert)
    return "esquema nacional de seguridad" in folded or bool(re.search(r"\bens\b", folded))


def _ens_level(text: str | None) -> int | None:
    levels = [ENS_LEVELS[w] for w in re.findall(r"\w+", fold(text or "")) if w in ENS_LEVELS]
    return max(levels) if levels else None


def _ens(cert: str, held: str | None, citation: Citation | None) -> Requirement:
    name, need, have = f"certification {cert}", _ens_level(cert), _ens_level(held)
    if need is None:
        return Requirement(name, "open", f"requires {cert}: no category named", citation)
    if have is not None and have >= need:
        return Requirement(name, "pass", f"requires {cert}: company is ENS {held}", citation)
    return Requirement(name, "fail", f"requires {cert}: company is ENS {held or 'none'}", citation)


def unestablished(fields: Mapping[str, FieldResult]) -> list[str]:
    """Solvency fields that did not come out usable, as "name (check)"."""
    out = []
    for name in SOLVENCY_FIELDS:
        f = fields.get(name)
        if f is None or not f.usable:
            out.append(f"{name} ({f.check if f else 'missing'})")
    return out


def check_requirements(
    fields: Mapping[str, FieldResult], p: CompanyProfile, tender: Tender | None = None
) -> list[Requirement]:
    s = p.solvency
    out: list[Requirement] = []
    months = _months(fields)
    for group in ("economic", "technical"):
        f = fields.get(f"{group}_solvency")
        if f and f.usable:
            out.append(_solvency(group, f, s, tender, months))

    certs = fields.get("certifications")
    if certs and certs.check == "verified" and certs.value:
        held = set().union(*(_numbers(c) for c in s.certifications))
        for cert in certs.value:
            name, numbers = f"certification {cert}", _numbers(str(cert))
            if _is_ens(str(cert)):
                out.append(_ens(str(cert), s.ens_level, certs.citation))
            elif not numbers:
                out.append(Requirement(name, "open", f"requires {cert}", certs.citation))
            elif numbers <= held:
                out.append(Requirement(name, "pass", f"requires {cert}: held", certs.citation))
            else:
                out.append(Requirement(name, "fail", f"requires {cert}: not held", certs.citation))

    classification = fields.get("classification")
    if classification and classification.check == "verified" and s.classification is None:
        out.append(
            Requirement(
                "classification",
                "open",
                f"asks classification {classification.value}; company has none",
                classification.citation,
            )
        )
    return out
