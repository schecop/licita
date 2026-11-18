from __future__ import annotations

from pathlib import Path

import pytest

from licita.cli import main
from licita.config import PlacspConfig
from licita.placsp.audit import audit
from licita.placsp.feed import FeedPage, parse_page
from tests.conftest import HEAD, PAGE2

LIMITS = PlacspConfig().audit_max_missing


def test_real_pages_pass_the_audit(head: FeedPage, page2: FeedPage) -> None:
    a = audit([head, page2], LIMITS)
    assert a.entries == 8
    assert a.skipped == 0
    assert a.warnings == []


def test_a_field_the_parser_stops_reading_is_flagged(head: FeedPage) -> None:
    # what a wrong XPath or a CODICE change looks like: no error, just None everywhere
    broken = head.model_copy(
        update={"tenders": [t.model_copy(update={"budget_eur": None}) for t in head.tenders]}
    )
    a = audit([broken], LIMITS)
    assert any(w.startswith("budget_eur missing in 100.0%") for w in a.warnings)


def test_an_entry_that_does_not_parse_is_counted_not_hidden() -> None:
    xml = HEAD.read_bytes().replace(
        b"</feed>", b"<entry><title>no folder status</title></entry></feed>"
    )
    page = parse_page(xml)
    assert page.skipped == 1
    assert "1 entries did not parse" in audit([page], LIMITS).warnings[0]


def test_audit_feed_command_reads_the_cache_only(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["audit-feed", str(HEAD), str(PAGE2)]) == 0
    assert "2 cached pages: 8 entries, 0 unparsed" in capsys.readouterr().out


def test_audit_feed_command_fails_on_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.atom"
    bad.write_bytes(HEAD.read_bytes().replace(b"</feed>", b"<entry/></feed>"))
    assert main(["audit-feed", str(bad)]) == 1
    assert "warning: 1 entries did not parse" in capsys.readouterr().out


def test_a_page_with_no_entries_is_flagged() -> None:
    empty = parse_page(b"<html><title>Error</title><body>denied<P/></body></html>")
    assert "1 pages with no entries" in audit([empty], LIMITS).warnings[0]
