"""Sanity checks on parsed feed pages: catch a parser that silently stops reading a field.

The parser never raises on a missing element (`_text` returns None), so a wrong XPath, or a
CODICE change on PLACSP's side, would empty a field in every tender without an error. A field that
is suddenly missing in many *open* tenders is that signature: open tenders (PUB) carry budget,
deadline and CPV almost always (baseline on 1,514 open tenders, 17 Nov 2026: 0% without budget or
deadline, 0.8% without CPV, 2.2% without documents, 9.1% without award criteria). Limits per field
live in config (`placsp.audit_max_missing`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from licita.placsp.feed import FeedPage, Tender


def _missing(t: Tender, name: str) -> bool:
    value = getattr(t, name)
    return value is None or value == "" or value == []


@dataclass
class FeedAudit:
    entries: int = 0
    skipped: int = 0  # entries the parser could not turn into a Tender
    open: int = 0
    missing: dict[str, int] = field(default_factory=dict)  # among open tenders
    warnings: list[str] = field(default_factory=list)

    def share(self, name: str) -> float:
        return self.missing.get(name, 0) / self.open if self.open else 0.0

    def report(self) -> str:
        lines = [f"{self.entries} entries, {self.skipped} unparsed, {self.open} open (PUB)"]
        lines += [
            f"  {name:20} missing in {n:5} open ({self.share(name):6.1%})"
            for name, n in sorted(self.missing.items(), key=lambda kv: -kv[1])
        ]
        lines += [f"warning: {w}" for w in self.warnings]
        return "\n".join(lines)


def audit(pages: Sequence[FeedPage], max_missing: Mapping[str, float]) -> FeedAudit:
    a = FeedAudit()
    for page in pages:
        a.entries += len(page.tenders) + page.skipped
        a.skipped += page.skipped
        for t in page.tenders:
            if t.status != "PUB":
                continue
            a.open += 1
            for name in max_missing:
                if _missing(t, name):
                    a.missing[name] = a.missing.get(name, 0) + 1
    empty = sum(1 for page in pages if not page.tenders and not page.skipped)
    if empty:
        a.warnings.append(f"{empty} pages with no entries at all (an error page that parsed?)")
    if a.skipped:
        a.warnings.append(
            f"{a.skipped} entries did not parse (no id, date or ContractFolderStatus)"
        )
    if a.open:
        for name, limit in max_missing.items():
            if a.share(name) > limit:
                a.warnings.append(
                    f"{name} missing in {a.share(name):.1%} of open tenders (limit {limit:.0%}): "
                    "parser or feed changed?"
                )
    return a
