"""The parser against values read by hand on each tender's PLACSP web page.

The other feed tests take their expected values from the same XML the parser reads, so a misread
CODICE field would pass them. Here the expected values come from a different source (the page a
person sees), so a misreading shows up as a mismatch.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from licita.placsp.feed import MADRID, Tender, parse_page

FIXTURES = Path(__file__).parent / "fixtures" / "placsp"
GOLDEN: list[dict[str, Any]] = yaml.safe_load((FIXTURES / "golden.yaml").read_text("utf-8"))
PARSED = {t.entry_id: t for t in parse_page((FIXTURES / "golden.atom").read_bytes()).tenders}


def _money(v: Any) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def _actual(t: Tender, name: str) -> Any:
    if name == "lot_budgets_eur":
        return [lot.budget_eur for lot in t.lots]
    if name == "award_criteria":
        return [(c.description.lower(), c.weight) for c in t.award_criteria]
    if name == "cpvs":
        return sorted(t.cpvs)
    return getattr(t, name)


def _expected(name: str, v: Any) -> Any:
    if name in ("budget_eur", "estimated_value_eur"):
        return _money(v)
    if name == "deadline":
        return None if v is None else datetime.fromisoformat(v).replace(tzinfo=MADRID)
    if name == "lot_budgets_eur":
        return [_money(x) for x in v or []]
    if name == "cpvs":
        return sorted(str(c) for c in v or [])
    if name == "award_criteria":
        return [(str(d).lower(), _money(w)) for d, w in v or []]
    return v


def _same(name: str, actual: Any, expected: Any) -> bool:
    if name == "award_criteria":
        # the golden value may hold only the start of a long description; order carries no
        # meaning (the notice groups formula and judgement criteria, the XML does not)
        def by_weight(cs: list[tuple[str, Decimal]]) -> list[tuple[str, Decimal]]:
            return sorted(cs, key=lambda c: (c[1], c[0]))

        return len(actual) == len(expected) and all(
            a_desc.startswith(e_desc) and a_w == e_w
            for (a_desc, a_w), (e_desc, e_w) in zip(
                by_weight(actual), by_weight(expected), strict=True
            )
        )
    return bool(actual == expected)


def test_every_golden_tender_is_in_the_fixture() -> None:
    assert {g["entry_id"] for g in GOLDEN} == PARSED.keys()


@pytest.mark.parametrize("g", GOLDEN, ids=[g["why"] for g in GOLDEN])
def test_parser_matches_the_web_page(g: dict[str, Any]) -> None:
    if not g.get("checked_on"):
        pytest.skip("not checked against the web page yet")
    t = PARSED[g["entry_id"]]
    fields = {k: v for k, v in g.items() if k not in ("entry_id", "why", "url", "checked_on")}
    todo = [k for k, v in fields.items() if v == "TODO"]
    assert not todo, f"checked_on is set but {todo} still TODO"
    wrong = {
        k: (_actual(t, k), _expected(k, v))
        for k, v in fields.items()
        if not _same(k, _actual(t, k), _expected(k, v))
    }
    assert not wrong, f"parser != web page (actual, expected): {wrong}"
