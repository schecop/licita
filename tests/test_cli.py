from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from licita.cli import main
from licita.config import AppConfig, PlacspConfig, load_config
from tests.conftest import HEAD, PAGE2


def test_budget_reports_the_cap(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["budget"]) == 0
    assert "of $15" in capsys.readouterr().out


def _cfg(tmp: Path) -> AppConfig:
    base = load_config(Path("config/example.yaml"))
    placsp = PlacspConfig(
        feed_url="https://example.test/head.atom",
        cache_dir=tmp / "atom",
        db_path=tmp / "licita.db",
        throttle_seconds=1,
    )
    return base.model_copy(update={"placsp": placsp})


def _client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        name = str(request.url).rsplit("/", 1)[-1]
        body = {"head.atom": HEAD, PAGE2.name: PAGE2}[name].read_bytes()
        return httpx.Response(200, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ingest_then_inbox_lists_the_candidates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("licita.placsp.fetch.time.sleep", lambda s: None)
    cfg = _cfg(tmp_path)
    assert main(["ingest", "--today", "2026-11-15"], client=_client(), cfg=cfg) == 0
    out = capsys.readouterr().out
    assert "2 pages (2 downloaded, 0 cached)" in out
    assert "7 tenders in the store" in out

    assert main(["inbox", "--today", "2026-11-16", "--no-go"], cfg=cfg) == 0
    out = capsys.readouterr().out
    # 6 open: Policía (candidate), Seg. Social (budget), Cantabria (budget + region),
    # and 3 outside CPV: backup (72910000), works (45211000), Taibilla (page 2)
    assert "1 candidates of 6 open tenders" in out
    assert "Opentext" in out
    assert "no lot within" in out  # lots: the reason is about the lots, not the total
    assert "Cantabria (ES13) outside" in out
    assert "+ 3 outside the profile's CPV codes" in out


def test_second_ingest_is_incremental(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("licita.placsp.fetch.time.sleep", lambda s: None)
    cfg = _cfg(tmp_path)
    main(["ingest", "--today", "2026-11-15"], client=_client(), cfg=cfg)
    capsys.readouterr()
    # next day: a new head file; the store already holds updates up to the 15th 19:49,
    # and the head reaches back past that, so page 2 is not needed
    assert main(["ingest", "--today", "2026-11-16"], client=_client(), cfg=cfg) == 0
    out = capsys.readouterr().out
    assert "1 pages (1 downloaded, 0 cached)" in out
    assert "0 new or updated" in out


def test_ingest_warns_when_the_page_cap_cuts_the_walk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("licita.placsp.fetch.time.sleep", lambda s: None)
    cfg = _cfg(tmp_path)
    cfg = cfg.model_copy(update={"placsp": cfg.placsp.model_copy(update={"max_pages": 1})})
    main(["ingest", "--today", "2026-11-15"], client=_client(), cfg=cfg)
    assert "warning: stopped at max_pages=1" in capsys.readouterr().out
