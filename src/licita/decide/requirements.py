"""Solvency the company plainly fails: decided by code from cited fields, before any model.

Only fields whose quote was verified can produce a code no-go (a no-go must cite its clause).
Each check answers pass, fail or open; "open" goes to the decision model with the field:
- Turnover: the pliego asks for the best year of the last three, which is what the profile holds:
  a straight comparison.
- Similar services: pliegos ask for an annual amount in the best year or a 3-year total; the
  profile holds the 3-year total. If even that total is below the requirement, it is a plain fail;
  otherwise it depends on the wording: open.
- ENS (Esquema Nacional de Seguridad) is compared by category, BÁSICA < MEDIA < ALTA, against the
  profile's `ens_level`; open if the pliego names no category. It is checked before the number
  match, because "ENS (RD 311/2022)" carries a number that is not a certification's.
- Other certifications with a number (ISO 9001, ISO/IEC 27001) are matched by that number; without
  one they are open.
- Classification required: open. For services it is usually an alternative to solvency, and
  whether it is here is wording, not a number.

`unestablished` is the other half: economic and technical solvency are in every pliego, so if
either did not come out verified (not found, uncited, or a quote that did not check), nothing was
compared and the tender goes to review by code. Without it a model's "go" with high confidence
would be auto-accepted over solvency nobody checked. Certifications, classification and staff are
not on that list: a pliego that does not mention them usually does not require them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from licita.extract.citations import Citation, FieldResult
from licita.profile import CompanyProfile
from licita.retrieval.text import fold

Verdict = Literal["pass", "fail", "open"]


@dataclass(frozen=True)
class Requirement:
    name: str
    verdict: Verdict
    reason: str
    citation: Citation | None


def _eur(v: Decimal) -> str:
    return f"€{v:,.0f}"


def _numbers(cert: str) -> set[str]:
    return set(re.findall(r"\d{4,5}", cert))


ENS_LEVELS = {"basica": 1, "basico": 1, "media": 2, "medio": 2, "alta": 3, "alto": 3}
SOLVENCY_FIELDS = ("min_annual_turnover_eur", "min_similar_services_eur")


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


def check_requirements(fields: Mapping[str, FieldResult], p: CompanyProfile) -> list[Requirement]:
    s = p.solvency
    out: list[Requirement] = []

    turnover = fields.get("min_annual_turnover_eur")
    if turnover and turnover.check == "verified":
        need = Decimal(str(turnover.value))
        ok = s.annual_turnover_eur >= need
        out.append(
            Requirement(
                "turnover",
                "pass" if ok else "fail",
                f"asks {_eur(need)} a year, company has {_eur(s.annual_turnover_eur)}",
                turnover.citation,
            )
        )

    similar = fields.get("min_similar_services_eur")
    if similar and similar.check == "verified":
        need = Decimal(str(similar.value))
        have = s.similar_contracts_last_3y_eur
        if have < need:
            out.append(
                Requirement(
                    "similar_services",
                    "fail",
                    f"asks {_eur(need)}, company's 3-year total is {_eur(have)}",
                    similar.citation,
                )
            )
        else:
            out.append(
                Requirement(
                    "similar_services",
                    "open",
                    f"asks {_eur(need)}; company's 3-year total {_eur(have)}: depends on "
                    "whether the pliego means one year or three",
                    similar.citation,
                )
            )

    certs = fields.get("certifications")
    if certs and certs.check == "verified" and certs.value:
        held = set().union(*(_numbers(c) for c in s.certifications))
        for cert in certs.value:
            numbers = _numbers(str(cert))
            if _is_ens(str(cert)):
                out.append(_ens(str(cert), s.ens_level, certs.citation))
            elif not numbers:
                out.append(
                    Requirement(f"certification {cert}", "open", f"requires {cert}", certs.citation)
                )
            elif numbers <= held:
                out.append(
                    Requirement(
                        f"certification {cert}", "pass", f"requires {cert}: held", certs.citation
                    )
                )
            else:
                out.append(
                    Requirement(
                        f"certification {cert}",
                        "fail",
                        f"requires {cert}: not held",
                        certs.citation,
                    )
                )

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
