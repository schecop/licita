from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from licita.placsp.feed import MADRID, FeedPage


def test_head_page_parses_every_entry_and_the_tombstone(head: FeedPage) -> None:
    assert len(head.tenders) == 6
    assert head.deleted == [
        "https://contrataciondelestado.es/sindicacion/licitacionesPerfilContratante/20904449"
    ]
    assert head.next_url is not None
    assert head.next_url.endswith("Completo3_20261114_200211.atom")


def test_tender_fields_come_from_codice(head: FeedPage) -> None:
    t = next(t for t in head.tenders if "Opentext" in t.title)
    assert t.status == "PUB"
    assert t.budget_eur == Decimal("88838.89")  # tax-exclusive amount
    assert all(c.startswith("72") for c in t.cpvs)
    assert t.nuts == "ES300"
    assert t.deadline == datetime(2026, 12, 15, 12, 0, tzinfo=MADRID)
    assert {d.kind for d in t.documents} >= {"legal", "technical"}
    assert len(t.award_criteria) == 3


def test_lots_carry_their_own_budget(head: FeedPage) -> None:
    t = next(t for t in head.tenders if t.budget_eur == Decimal("54331470.4"))
    assert [lot.budget_eur for lot in t.lots] == [Decimal("25755378.14"), Decimal("28576092.26")]


def test_oldest_is_the_earliest_update(page2: FeedPage) -> None:
    assert page2.oldest is not None
    assert page2.oldest.date().isoformat() == "2026-11-12"
