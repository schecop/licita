"""Walk the PLACSP ATOM feed back to a date, cache-first and throttled.

PLACSP is a public service: every page is saved under `data/raw/atom/` and never downloaded
twice. Older pages have a timestamp in their name (`..._20261114_200211.atom`) and never change, so
the file name is the cache key. The head page changes all day, so it is cached once per day
(`<date>-head.atom`); `refresh=True` re-reads it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import httpx

from licita.placsp.feed import FeedPage, parse_page

USER_AGENT = "licita/0.1 (public-tender research prototype)"


@dataclass
class Walk:
    pages: list[FeedPage] = field(default_factory=list)
    downloaded: int = 0
    cached: int = 0
    complete: bool = False  # reached back past `since` (False: stopped at max_pages)


@dataclass
class FeedFetcher:
    client: httpx.Client
    cache_dir: Path
    feed_url: str
    throttle_s: float = 2.0
    max_pages: int = 20
    sleep: Callable[[float], None] = time.sleep
    _last_request: float | None = None

    def _get(self, url: str, path: Path, refresh: bool, walk: Walk) -> bytes:
        if path.exists() and not refresh:
            walk.cached += 1
            return path.read_bytes()
        if self._last_request is not None:
            self.sleep(self.throttle_s)
        response = self.client.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
        self._last_request = time.monotonic()
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        walk.downloaded += 1
        return response.content

    def walk_since(self, since: datetime, today: date, refresh: bool = False) -> Walk:
        """Head page first, then `next` links, until a page reaches back past `since`."""
        walk = Walk()
        url: str | None = self.feed_url
        path = self.cache_dir / f"{today.isoformat()}-head.atom"
        while url is not None and len(walk.pages) < self.max_pages:
            page = parse_page(self._get(url, path, refresh and not walk.pages, walk))
            walk.pages.append(page)
            if page.oldest is None or page.oldest < since or page.next_url is None:
                walk.complete = True
                break
            url = page.next_url
            path = self.cache_dir / url.rsplit("/", 1)[-1] if url else path
        return walk
