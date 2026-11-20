"""PDF -> text per page, with PyMuPDF. The page number is what a citation points to.

`sort=True` reads blocks top-to-bottom, left-to-right, so two-column layouts and headers come out
in reading order instead of creation order. A page with almost no text is most likely a scan:
it is kept (its number still exists) and flagged, never silently dropped, so a missing answer can
be traced to "the clause was in a scanned annex" rather than to retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

SCANNED_BELOW = 40  # characters of text on a page under which it is treated as a scan


@dataclass(frozen=True)
class Page:
    number: int  # 1-based, as a person reads it in a PDF viewer
    text: str

    @property
    def scanned(self) -> bool:
        return len(self.text.strip()) < SCANNED_BELOW


def read_pdf(path: Path) -> list[Page]:
    with pymupdf.open(path) as doc:
        return [Page(i + 1, page.get_text("text", sort=True)) for i, page in enumerate(doc)]
