from __future__ import annotations

from pathlib import Path

import pytest

from licita.placsp.feed import FeedPage
from licita.store import Store


@pytest.mark.parametrize("head_first", [True, False])
def test_newest_version_wins_whatever_the_order(
    tmp_path: Path, head: FeedPage, page2: FeedPage, head_first: bool
) -> None:
    store = Store(tmp_path / "t.db")
    for page in [head, page2] if head_first else [page2, head]:
        store.upsert(page.tenders)
    policia = [t for t in store.tenders() if "Opentext" in t.title]
    assert [t.status for t in policia] == ["PUB"]  # the PRE on page 2 is older
    assert store.count() == 7  # 6 on the head + 1 only on page 2
    store.close()


def test_reingesting_the_same_page_changes_nothing(tmp_path: Path, head: FeedPage) -> None:
    store = Store(tmp_path / "t.db")
    assert store.upsert(head.tenders) == 6
    assert store.upsert(head.tenders) == 0
    store.close()


def test_tombstone_marks_the_tender_withdrawn(tmp_path: Path, head: FeedPage) -> None:
    store = Store(tmp_path / "t.db")
    store.upsert(head.tenders)
    target = head.tenders[0].entry_id
    assert store.mark_deleted([target, "unknown"]) == 1
    withdrawn = store.tenders(status="ANUL")
    assert [t.entry_id for t in withdrawn] == [target]
    assert withdrawn[0].status == "ANUL"
    store.close()
