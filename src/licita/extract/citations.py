"""Every quote a model returns is checked by code against the chunk it names.

A citation is only as good as this check: a model can return a plausible clause that is not in
the pliego, or the right clause under the wrong chunk id. Matching is on folded text (case,
accents and layout whitespace ignored), and a quote with an ellipsis ("... hasta ...") matches if
its pieces appear in the chunk in order.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from licita.documents.chunk import Chunk
from licita.retrieval.text import fold

Check = Literal["verified", "explicit_none", "not_found", "uncited", "unknown_chunk", "bad_quote"]


@dataclass(frozen=True)
class Citation:
    chunk_id: str
    doc: str
    page: int
    quote: str


@dataclass(frozen=True)
class FieldResult:
    name: str
    value: Any
    check: Check
    citation: Citation | None  # set only when the quote was verified

    @property
    def usable(self) -> bool:
        """A value code may act on: found and cited, or explicitly absent and cited."""
        return self.check in ("verified", "explicit_none")


def quote_in(quote: str, text: str) -> bool:
    pieces = [p for p in (fold(x) for x in re.split(r"\.\.\.|…", quote)) if p]
    haystack, at = fold(text), 0
    for piece in pieces:
        found = haystack.find(piece, at)
        if found < 0:
            return False
        at = found + len(piece)
    return bool(pieces)


def _empty(value: Any) -> bool:
    return value is None or value == []


def check_field(
    name: str, raw: Mapping[str, Any] | None, chunks: Mapping[str, Chunk]
) -> FieldResult:
    raw = raw or {}
    value, chunk_id, quote = raw.get("value"), raw.get("chunk_id"), raw.get("quote")
    if not chunk_id or not quote:
        return FieldResult(name, value, "not_found" if _empty(value) else "uncited", None)
    chunk = chunks.get(chunk_id)
    if chunk is None:
        return FieldResult(name, value, "unknown_chunk", None)
    if not quote_in(quote, chunk.text):
        return FieldResult(name, value, "bad_quote", None)
    citation = Citation(chunk.id, chunk.doc, chunk.page, quote)
    return FieldResult(name, value, "explicit_none" if _empty(value) else "verified", citation)
