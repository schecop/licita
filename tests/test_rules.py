from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from licita.placsp.feed import MADRID, FeedPage, Lot, Tender
from licita.profile import load_profile
from licita.rules import cpv_prefix, screen

PROFILE = load_profile(Path("data/company/profile.yaml"))
NOW = datetime(2026, 11, 16, 9, 0, tzinfo=MADRID)


def _verdicts(t: Tender, now: datetime = NOW) -> dict[str, str]:
    return {r.rule: r.verdict for r in screen(t, PROFILE, now, 5).results}


def _policia(page: FeedPage) -> Tender:
    return next(t for t in page.tenders if "Opentext" in t.title)


def test_cpv_prefix_drops_trailing_zeros() -> None:
    assert cpv_prefix("72200000") == "722"
    assert cpv_prefix("72000000") == "72"
    assert cpv_prefix("70000000") == "70"  # never below the division


def test_division_70_is_not_broader_than_72(head: FeedPage) -> None:
    t = _policia(head).model_copy(update={"cpvs": ["70000000"]})
    assert _verdicts(t)["cpv"] == "fail"


def test_patrimonial_contract_is_a_no_go(head: FeedPage) -> None:
    # real case: TGSS property auctions, no CPV, type 50
    t = _policia(head).model_copy(update={"contract_type": "50", "cpvs": []})
    s = screen(t, PROFILE, NOW, 5)
    assert [r.rule for r in s.failed] == ["type"]


def test_it_tender_in_madrid_within_budget_is_a_candidate(head: FeedPage) -> None:
    s = screen(_policia(head), PROFILE, NOW, 5)
    assert not s.no_go
    assert {r.verdict for r in s.results} == {"pass"}


def test_works_contract_fails_on_cpv(head: FeedPage) -> None:
    t = next(t for t in head.tenders if t.folder_id == "04101/2026/250-O")
    assert _verdicts(t)["cpv"] == "fail"


def test_big_tender_fails_on_budget_even_with_lots(head: FeedPage) -> None:
    t = next(t for t in head.tenders if t.budget_eur == Decimal("54331470.4"))
    s = screen(t, PROFILE, NOW, 5)
    assert [r.rule for r in s.failed] == ["budget"]
    assert "no lot within" in s.failed[0].reason


def test_cantabria_fails_on_budget_and_region(head: FeedPage) -> None:
    s = screen(next(t for t in head.tenders if t.nuts == "ES13"), PROFILE, NOW, 5)
    assert {r.rule for r in s.failed} == {"budget", "region"}
    assert "Cantabria" in next(r.reason for r in s.failed if r.rule == "region")


def test_generic_cpv_is_kept_as_broader(head: FeedPage) -> None:
    t = _policia(head).model_copy(update={"cpvs": ["72000000"]})
    r = next(r for r in screen(t, PROFILE, NOW, 5).results if r.rule == "cpv")
    assert (r.verdict, "broader" in r.reason) == ("pass", True)


def test_backup_services_are_outside_the_profile(head: FeedPage) -> None:
    t = next(t for t in head.tenders if "backup" in t.title)
    assert _verdicts(t)["cpv"] == "fail"  # 72910000: not 722 / 723 / 7226 / 726


def test_resolved_tender_is_not_open(head: FeedPage) -> None:
    t = next(t for t in head.tenders if t.status == "RES")
    assert _verdicts(t)["status"] == "fail"


@pytest.mark.parametrize(
    ("now", "verdict"),
    [
        (datetime(2026, 12, 1, tzinfo=MADRID), "pass"),
        (datetime(2026, 12, 12, tzinfo=MADRID), "fail"),  # 3 days left, under 5
        (datetime(2026, 12, 16, tzinfo=MADRID), "fail"),  # passed
    ],
)
def test_deadline_needs_time_to_prepare(head: FeedPage, now: datetime, verdict: str) -> None:
    assert _verdicts(_policia(head), now)["deadline"] == verdict


def test_one_lot_in_range_is_enough(head: FeedPage) -> None:
    t = _policia(head).model_copy(
        update={
            "budget_eur": Decimal(2_000_000),
            "lots": [
                Lot(lot_id="1", name=None, budget_eur=Decimal(1_500_000), cpvs=[]),
                Lot(lot_id="2", name=None, budget_eur=Decimal(500_000), cpvs=[]),
            ],
        }
    )
    assert _verdicts(t)["budget"] == "pass"


def test_missing_feed_data_is_open_not_fail(head: FeedPage) -> None:
    t = _policia(head).model_copy(
        update={"budget_eur": None, "nuts": None, "deadline": None, "cpvs": []}
    )
    s = screen(t, PROFILE, NOW, 5)
    assert not s.no_go
    assert {r.rule for r in s.open} == {"cpv", "budget", "deadline", "region"}


def test_nationwide_code_matches_estatal(head: FeedPage) -> None:
    t = _policia(head).model_copy(update={"nuts": "ES"})
    assert _verdicts(t)["region"] == "pass"
