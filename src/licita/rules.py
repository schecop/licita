"""Deterministic screening: the plain no-gos, decided by code with the reason.

Each rule answers pass, fail or open. One fail is a no-go; "open" means the feed does not say
(no budget, no place of execution) and the question goes on to the documents. Rules never say
"go": a tender that passes every rule is only a candidate, and the decision needs the pliegos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from licita.placsp.feed import Tender
from licita.profile import CompanyProfile

Verdict = Literal["pass", "fail", "open"]

# NUTS-2 regions of Spain (NUTS 2021), as the profile names them. "ES" alone = the whole country.
NUTS2 = {
    "ES11": "Galicia",
    "ES12": "Principado de Asturias",
    "ES13": "Cantabria",
    "ES21": "País Vasco",
    "ES22": "Comunidad Foral de Navarra",
    "ES23": "La Rioja",
    "ES24": "Aragón",
    "ES30": "Comunidad de Madrid",
    "ES41": "Castilla y León",
    "ES42": "Castilla-La Mancha",
    "ES43": "Extremadura",
    "ES51": "Cataluña",
    "ES52": "Comunidad Valenciana",
    "ES53": "Illes Balears",
    "ES61": "Andalucía",
    "ES62": "Región de Murcia",
    "ES63": "Ceuta",
    "ES64": "Melilla",
    "ES70": "Canarias",
}
NATIONWIDE = "Estatal"


@dataclass(frozen=True)
class RuleResult:
    rule: str
    verdict: Verdict
    reason: str


@dataclass(frozen=True)
class Screening:
    tender: Tender
    results: list[RuleResult]

    @property
    def no_go(self) -> bool:
        return any(r.verdict == "fail" for r in self.results)

    @property
    def failed(self) -> list[RuleResult]:
        return [r for r in self.results if r.verdict == "fail"]

    @property
    def open(self) -> list[RuleResult]:
        return [r for r in self.results if r.verdict == "open"]


def _eur(x: Decimal) -> str:
    return f"€{x:,.0f}"


def cpv_prefix(code: str) -> str:
    """'72200000' -> '722': CPV codes are hierarchical, trailing zeros mean 'any below'.

    Never shorter than the 2-digit division: '70000000' is division 70, not '7'.
    """
    stripped = code.rstrip("0")
    return stripped if len(stripped) >= 2 else code[:2]


def check_status(t: Tender) -> RuleResult:
    if t.status == "PUB":
        return RuleResult("status", "pass", "open for bids (PUB)")
    return RuleResult("status", "fail", f"not open for bids ({t.status})")


PATRIMONIAL = "50"  # sale or lease of public property: not a contract a company bids to deliver


def check_contract_type(t: Tender) -> RuleResult:
    if t.contract_type == PATRIMONIAL:
        return RuleResult("type", "fail", "patrimonial (sale or lease of public property)")
    return RuleResult("type", "pass", f"contract type {t.contract_type or 'not given'}")


def check_cpv(t: Tender, p: CompanyProfile) -> RuleResult:
    codes = sorted({*t.cpvs, *(c for lot in t.lots for c in lot.cpvs)})
    if not codes:
        return RuleResult("cpv", "open", "no CPV in the feed")
    prefixes = [cpv_prefix(c) for c in p.cpv_prefixes]
    hits = [c for c in codes if any(c.startswith(pre) for pre in prefixes)]
    if hits:
        return RuleResult("cpv", "pass", f"CPV {', '.join(hits)} in the profile")
    # a generic code (72000000, "IT services") covers the profile's narrower ones: keep it,
    # the documents say what the work really is
    broader = [c for c in codes if any(pre.startswith(cpv_prefix(c)) for pre in prefixes)]
    if broader:
        return RuleResult("cpv", "pass", f"CPV {', '.join(broader)} is broader than the profile")
    return RuleResult("cpv", "fail", f"CPV {', '.join(codes)} outside the profile")


def check_budget(t: Tender, p: CompanyProfile) -> RuleResult:
    lo, hi = p.budget_eur.min, p.budget_eur.max
    span = f"{_eur(lo)}-{_eur(hi)}"
    lots = [lot for lot in t.lots if lot.budget_eur is not None]
    if lots:  # a company can bid for a single lot
        fits = [lot for lot in lots if lo <= lot.budget_eur <= hi]  # type: ignore[operator]
        if fits:
            names = ", ".join(f"lot {lot.lot_id} {_eur(lot.budget_eur)}" for lot in fits)  # type: ignore[arg-type]
            return RuleResult("budget", "pass", f"{names} within {span}")
        return RuleResult("budget", "fail", f"no lot within {span}")
    if t.budget_eur is None:
        return RuleResult("budget", "open", "no budget in the feed")
    if lo <= t.budget_eur <= hi:
        return RuleResult("budget", "pass", f"{_eur(t.budget_eur)} within {span}")
    side = "below" if t.budget_eur < lo else "above"
    return RuleResult("budget", "fail", f"{_eur(t.budget_eur)} {side} {span}")


def check_deadline(t: Tender, now: datetime, min_days: int) -> RuleResult:
    if t.deadline is None:
        return RuleResult("deadline", "open", "no deadline in the feed")
    left = t.deadline - now
    when = t.deadline.strftime("%Y-%m-%d %H:%M")
    if left < timedelta(0):
        return RuleResult("deadline", "fail", f"deadline passed ({when})")
    if left < timedelta(days=min_days):
        return RuleResult("deadline", "fail", f"{left.days} days left, under {min_days} ({when})")
    return RuleResult("deadline", "pass", f"{left.days} days left ({when})")


def check_region(t: Tender, p: CompanyProfile) -> RuleResult:
    if t.nuts is None:
        return RuleResult("region", "open", "no place of execution in the feed")
    name = NATIONWIDE if t.nuts == "ES" else NUTS2.get(t.nuts[:4])
    if name is None:
        return RuleResult("region", "open", f"unknown NUTS code {t.nuts}")
    if name in p.regions:
        return RuleResult("region", "pass", f"{name} ({t.nuts})")
    return RuleResult("region", "fail", f"{name} ({t.nuts}) outside the profile's regions")


def screen(t: Tender, p: CompanyProfile, now: datetime, min_days_to_deadline: int) -> Screening:
    return Screening(
        t,
        [
            check_status(t),
            check_contract_type(t),
            check_cpv(t, p),
            check_budget(t, p),
            check_deadline(t, now, min_days_to_deadline),
            check_region(t, p),
        ],
    )
