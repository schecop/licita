"""SQLite store of tenders: one row per ATOM entry id, newest version wins.

One file, standard library, and an upsert that makes the order pages are read in irrelevant.
The full parsed tender is kept as JSON; only the columns the queries filter on are real columns.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from licita.placsp.feed import Tender

SCHEMA = """
create table if not exists tenders (
    entry_id    text primary key,
    updated_utc text not null,  -- UTC so that text order is time order
    status      text not null,
    data        text not null,  -- Tender as JSON
    ingested_at text not null
);
create index if not exists tenders_status on tenders(status);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def upsert(self, tenders: Iterable[Tender]) -> int:
        """Insert, or replace when the incoming version is newer. Returns rows changed."""
        now = datetime.now(UTC).isoformat()
        before = self.db.total_changes
        self.db.executemany(
            """
            insert into tenders (entry_id, updated_utc, status, data, ingested_at)
            values (?, ?, ?, ?, ?)
            on conflict(entry_id) do update set
                updated_utc = excluded.updated_utc, status = excluded.status,
                data = excluded.data, ingested_at = excluded.ingested_at
            where excluded.updated_utc > tenders.updated_utc
            """,
            [
                (
                    t.entry_id,
                    t.updated.astimezone(UTC).isoformat(),
                    t.status,
                    t.model_dump_json(),
                    now,
                )
                for t in tenders
            ],
        )
        self.db.commit()
        return self.db.total_changes - before

    def mark_deleted(self, entry_ids: Iterable[str]) -> int:
        """A tombstone in the feed: the tender was withdrawn (ANULADA)."""
        before = self.db.total_changes
        self.db.executemany(
            "update tenders set status = 'ANUL', data = json_set(data, '$.status', 'ANUL') "
            "where entry_id = ?",
            [(e,) for e in entry_ids],
        )
        self.db.commit()
        return self.db.total_changes - before

    def tenders(self, status: str | None = None) -> list[Tender]:
        sql, args = "select data from tenders", tuple[str, ...]()
        if status is not None:
            sql, args = sql + " where status = ?", (status,)
        rows = self.db.execute(sql + " order by updated_utc desc", args).fetchall()
        return [Tender.model_validate_json(r[0]) for r in rows]

    def get(self, entry: str) -> Tender | None:
        """By full ATOM entry id or by its last part (the number in the PLACSP URL)."""
        row = self.db.execute(
            "select data from tenders where entry_id = ? or entry_id like ?", (entry, f"%/{entry}")
        ).fetchone()
        return Tender.model_validate_json(row[0]) if row else None

    def newest(self) -> datetime | None:
        """The latest update already stored: where an incremental ingest can stop."""
        row = self.db.execute("select max(updated_utc) from tenders").fetchone()
        return datetime.fromisoformat(row[0]) if row[0] else None

    def count(self) -> int:
        return int(self.db.execute("select count(*) from tenders").fetchone()[0])
