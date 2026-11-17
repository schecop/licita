from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from licita.placsp.feed import MADRID
from licita.placsp.fetch import FeedFetcher
from tests.conftest import HEAD, PAGE2

FEED = "https://example.test/feed/head.atom"
SINCE, TODAY = datetime(2026, 11, 14, tzinfo=MADRID), date(2026, 11, 16)


def _fetcher(tmp: Path, seen: list[str], sleeps: list[float] | None = None) -> FeedFetcher:
    """Serves the fixture pages: the head at FEED, page 2 at its real file name on PLACSP."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url == FEED:
            return httpx.Response(200, content=HEAD.read_bytes())
        if url.endswith(PAGE2.name):
            return httpx.Response(200, content=PAGE2.read_bytes())
        return httpx.Response(404)

    return FeedFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache_dir=tmp,
        feed_url=FEED,
        throttle_s=2.0,
        sleep=(sleeps if sleeps is not None else []).append,
    )


def test_walks_next_links_until_past_since_and_throttles(tmp_path: Path) -> None:
    seen: list[str] = []
    sleeps: list[float] = []
    walk = _fetcher(tmp_path, seen, sleeps).walk_since(SINCE, TODAY)
    # page 2 reaches back to the 12th: stop there, never ask for page 3
    assert len(walk.pages) == 2
    assert walk.complete
    assert walk.downloaded == 2
    assert len(seen) == 2
    assert sleeps == [2.0]  # one wait, between the two requests
    assert (tmp_path / "2026-11-16-head.atom").exists()
    assert (tmp_path / PAGE2.name).exists()


def test_second_run_reads_the_cache_only(tmp_path: Path) -> None:
    seen: list[str] = []
    _fetcher(tmp_path, seen).walk_since(SINCE, TODAY)
    seen.clear()
    walk = _fetcher(tmp_path, seen).walk_since(SINCE, TODAY)
    assert seen == []
    assert walk.cached == 2


def test_refresh_redownloads_only_the_head(tmp_path: Path) -> None:
    seen: list[str] = []
    _fetcher(tmp_path, seen).walk_since(SINCE, TODAY)
    seen.clear()
    _fetcher(tmp_path, seen).walk_since(SINCE, TODAY, refresh=True)
    assert seen == [FEED]


def test_stops_on_the_head_when_it_already_covers_since(tmp_path: Path) -> None:
    walk = _fetcher(tmp_path, []).walk_since(datetime(2026, 11, 15, tzinfo=MADRID), TODAY)
    assert len(walk.pages) == 1


def test_max_pages_stops_the_walk_and_says_so(tmp_path: Path) -> None:
    fetcher = _fetcher(tmp_path, [])
    fetcher.max_pages = 1
    walk = fetcher.walk_since(SINCE, TODAY)
    assert (len(walk.pages), walk.complete) == (1, False)


def test_http_error_raises_and_caches_nothing(tmp_path: Path) -> None:
    fetcher = FeedFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))),
        cache_dir=tmp_path,
        feed_url=FEED,
    )
    with pytest.raises(httpx.HTTPStatusError):
        fetcher.walk_since(SINCE, TODAY)
    assert list(tmp_path.iterdir()) == []
