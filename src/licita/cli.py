"""`licita` command line. Subcommands arrive with their slice (ingest, inbox, analyse, eval...)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta

import httpx

from licita import budget
from licita.config import AppConfig, load_config
from licita.placsp.feed import MADRID
from licita.placsp.fetch import FeedFetcher
from licita.profile import load_profile
from licita.rules import Screening, screen
from licita.store import Store


def _ingest(
    cfg: AppConfig, today: date, days: int | None, refresh: bool, client: httpx.Client
) -> None:
    """Incremental by default: walk back to the newest update already stored.

    An empty store (or --days) walks back to midnight N days ago. A weekday is ~3,000 updates,
    ~7 pages of ~15 MB, so a first run is the expensive one.
    """
    store = Store(cfg.placsp.db_path)
    newest = store.newest()
    if days is None and newest is not None:
        since = newest
    else:
        since = datetime.combine(today - timedelta(days=days or 1), time.min, MADRID)
    fetcher = FeedFetcher(
        client=client,
        cache_dir=cfg.placsp.cache_dir,
        feed_url=cfg.placsp.feed_url,
        throttle_s=cfg.placsp.throttle_seconds,
        max_pages=cfg.placsp.max_pages,
    )
    try:
        walk = fetcher.walk_since(since, today, refresh=refresh)
        changed = sum(store.upsert(p.tenders) for p in walk.pages)
        deleted = sum(store.mark_deleted(p.deleted) for p in walk.pages)
        entries = sum(len(p.tenders) for p in walk.pages)
        print(
            f"{len(walk.pages)} pages ({walk.downloaded} downloaded, {walk.cached} cached), "
            f"{entries} entries since {since:%Y-%m-%d %H:%M}: {changed} new or updated, "
            f"{deleted} withdrawn; {store.count()} tenders in the store"
        )
        if not walk.complete:
            oldest = min(p.oldest for p in walk.pages if p.oldest is not None)
            print(
                f"warning: stopped at max_pages={cfg.placsp.max_pages}; "
                f"updates between {since:%Y-%m-%d %H:%M} and {oldest:%Y-%m-%d %H:%M} not read"
            )
    finally:
        store.close()


def _line(s: Screening) -> str:
    t = s.tender
    deadline = f"{t.deadline:%d %b}" if t.deadline else "—"
    money = f"€{t.budget_eur:,.0f}" if t.budget_eur is not None else "€?"
    return f"{deadline:>6}  {money:>12}  {t.title[:80]}\n{'':22}{t.buyer or ''}"


def _inbox(cfg: AppConfig, now: datetime, show_no_go: bool) -> None:
    profile = load_profile(cfg.rules.profile_path)
    store = Store(cfg.placsp.db_path)
    try:
        tenders = store.tenders(status="PUB")
    finally:
        store.close()
    screened = [screen(t, profile, now, cfg.rules.min_days_to_deadline) for t in tenders]
    candidates = sorted(
        (s for s in screened if not s.no_go), key=lambda s: s.tender.deadline or now
    )
    print(f"{len(candidates)} candidates of {len(screened)} open tenders for {profile.name}\n")
    for s in candidates:
        print(_line(s))
        for r in s.open:
            print(f"{'':22}? {r.rule}: {r.reason}")
    if show_no_go:
        print("\nno-go by rules:")
        for s in screened:
            if s.no_go and any(r.rule == "cpv" and r.verdict == "pass" for r in s.results):
                print(_line(s))
                for r in s.failed:
                    print(f"{'':22}✗ {r.rule}: {r.reason}")
        others = sum(
            1 for s in screened if any(r.rule == "cpv" and r.verdict == "fail" for r in s.failed)
        )
        print(f"\n(+ {others} outside the profile's CPV codes, not listed)")


def main(
    argv: Sequence[str] | None = None,
    client: httpx.Client | None = None,
    cfg: AppConfig | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="licita",
        description="Match public tenders (PLACSP) against a company profile, with cited go/no-go.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("budget", help="show what the paid API calls have cost so far")
    ingest = sub.add_parser("ingest", help="read the PLACSP feed back N days into the store")
    ingest.add_argument(
        "--days",
        type=int,
        default=None,
        help="walk back to midnight N days ago (default: since the newest stored update)",
    )
    ingest.add_argument("--today", type=date.fromisoformat, default=None)
    ingest.add_argument("--refresh", action="store_true", help="re-download today's head page")
    inbox = sub.add_parser("inbox", help="open tenders that pass the deterministic rules")
    inbox.add_argument("--today", type=date.fromisoformat, default=None)
    inbox.add_argument("--no-go", action="store_true", help="also list IT tenders the rules drop")
    args = parser.parse_args(argv)

    if args.command == "budget":
        print(f"spent ${budget.spent():.4f} of ${budget.CAP_USD}")
        return 0
    cfg = cfg or load_config()
    today = args.today or datetime.now(MADRID).date()
    if args.command == "ingest":
        with client or httpx.Client(follow_redirects=True) as c:
            _ingest(cfg, today, args.days, args.refresh, c)
    elif args.command == "inbox":
        now = (
            datetime.now(MADRID)
            if args.today is None
            else datetime.combine(today, time.min, MADRID)
        )
        _inbox(cfg, now, args.no_go)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
