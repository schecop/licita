"""`licita` command line. Subcommands arrive with their slice (ingest, inbox, analyse, eval...)."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path

import httpx

from licita import budget
from licita.config import AppConfig, load_config
from licita.graph import Deps, Event, stream
from licita.llm.factory import LazyLLM
from licita.llm.metered import Call, Metered
from licita.placsp.audit import audit
from licita.placsp.feed import MADRID, parse_page
from licita.placsp.fetch import FeedFetcher, NotAFeedError
from licita.profile import load_profile
from licita.retrieval.embed import HashingEmbedder
from licita.rules import Screening, screen
from licita.store import Store
from licita.tracing import LangfuseExporter, Trace


def _ingest(
    cfg: AppConfig, today: date, days: int | None, refresh: bool, client: httpx.Client
) -> int:
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
        try:
            walk = fetcher.walk_since(since, today, refresh=refresh)
        except NotAFeedError as e:
            print(f"error: {e}")
            print("PLACSP may be down for maintenance; nothing was stored.")
            return 1
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
        for w in audit(walk.pages, cfg.placsp.audit_max_missing).warnings:
            print(f"warning: {w}")
    finally:
        store.close()
    return 0


def _audit_feed(cfg: AppConfig, paths: Sequence[Path]) -> int:
    """Re-parse cached pages (no network) and check field coverage. Exit 1 on any warning."""
    files = list(paths) or sorted(cfg.placsp.cache_dir.glob("*.atom"))
    pages = [parse_page(f.read_bytes()) for f in files]
    result = audit(pages, cfg.placsp.audit_max_missing)
    print(f"{len(files)} cached pages: {result.report()}")
    return 1 if result.warnings else 0


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


def print_event(e: Event) -> None:
    s = dict(e.summary)  # a copy: the event itself stays whole
    o = s.pop("outcome", None)
    detail = json.dumps(s, ensure_ascii=False, default=str) if s else ""
    print(f"  ✓ {e.node:15} {detail}")
    if o:
        print(f"  → {o['decision'] or 'sin decisión'} ({o['route']}, por {o['by']}): {o['reason']}")


def _analyse(cfg: AppConfig, entry: str, now: datetime) -> int:
    """One tender through the graph with the models in config. Paid calls happen only if the
    tender passes the rules and has documents in `analysis.pliegos_dir`; the estimate is printed
    and checked against the cap first."""
    store = Store(cfg.placsp.db_path)
    try:
        tender = store.get(entry)
    finally:
        store.close()
    if tender is None:
        print(f"no tender {entry} in the store (run `licita ingest`)")
        return 1
    calls: list[Call] = []
    roles = {}
    for role in ("extraction", "decision"):
        choice = cfg.llm[role]
        roles[role] = Metered(
            LazyLLM(choice), role, choice.usd_per_mtok_in, choice.usd_per_mtok_out, calls
        )
    prices = {r: m.prices for r, m in roles.items()}
    print(budget.check(budget.estimate(1, prices)))
    print(tender.title[:90])
    print(tender.buyer or "")
    print("warning: embeddings = hashing (bag of words) until the retrieval comparison picks one")
    deps = Deps(
        cfg,
        load_profile(cfg.rules.profile_path),
        roles["extraction"],
        roles["decision"],
        HashingEmbedder(),
        now,
        extraction_mode=cfg.llm["extraction"].structured_output,
        calls=calls,
    )
    exporter = LangfuseExporter.from_env()
    trace = Trace("tender", tender_id=tender.entry_id) if exporter else None

    async def run() -> None:
        try:
            async for item in stream(deps, tender, trace):
                if isinstance(item, Event):
                    print_event(item)
            if exporter and trace:
                await exporter.export(trace)
        finally:
            for m in roles.values():
                await m.aclose()
            if exporter:
                await exporter.aclose()

    asyncio.run(run())
    spent = budget.record(calls, run_id=f"analyse-{entry}")
    print(f"{len(calls)} model calls, ${spent:.4f}")
    return 0


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
    audit_feed = sub.add_parser(
        "audit-feed", help="re-parse cached feed pages and check how complete the fields are"
    )
    audit_feed.add_argument("pages", nargs="*", type=Path, help="default: every cached page")
    analyse = sub.add_parser("analyse", help="run one tender through the analysis graph")
    analyse.add_argument("entry", help="ATOM entry id, or its number (e.g. 20913966)")
    analyse.add_argument("--today", type=date.fromisoformat, default=None)
    inbox = sub.add_parser("inbox", help="open tenders that pass the deterministic rules")
    inbox.add_argument("--today", type=date.fromisoformat, default=None)
    inbox.add_argument("--no-go", action="store_true", help="also list IT tenders the rules drop")
    args = parser.parse_args(argv)
    # Windows consoles and pipes default to cp1252: Spanish text and ✓ would crash print()
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.command == "budget":
        print(f"spent ${budget.spent():.4f} of ${budget.CAP_USD}")
        return 0
    cfg = cfg or load_config()
    if args.command == "audit-feed":
        return _audit_feed(cfg, args.pages)
    today = args.today or datetime.now(MADRID).date()
    if args.command == "ingest":
        with client or httpx.Client(follow_redirects=True) as c:
            return _ingest(cfg, today, args.days, args.refresh, c)
    elif args.command == "analyse":
        now = (
            datetime.now(MADRID) if args.today is None else datetime.combine(today, time(9), MADRID)
        )
        return _analyse(cfg, args.entry, now)
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
