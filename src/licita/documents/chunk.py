"""Pages -> chunks a retriever can rank and a model can cite.

Rules that keep citations honest:
- A chunk never crosses a page: its page number is the citation's page.
- Chunks are built from whole paragraphs, up to `max_chars`; a paragraph longer than that is cut
  at sentence ends. Neighbouring chunks share one paragraph (`overlap`), so a clause split
  across a paragraph boundary is whole in at least one chunk.
- The id says where the chunk is (`<doc>:p<page>:<n>`), so a trace or a review screen can show
  the source without a lookup.
- Each chunk carries the section it falls under ("4. SOLVENCIA ECONÓMICA Y FINANCIERA > 4.2 ..."),
  carried across pages. A long clause spans several chunks and only the first holds its heading;
  without it, "por importe igual o superior a 300.000 euros" in the third chunk of the solvency
  clause would not say it is solvency, and the solvency query would miss it. The section is
  indexed with the text and shown to the model, but it is not part of `text`: quotes are checked
  against `text` only, so a citation never lands on a heading printed pages earlier.

PDF text breaks lines at the layout width; lines are re-joined into paragraphs (a blank line,
or a line that ends a sentence and is followed by a capital or a numbered heading, ends one).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from licita.documents.pdf import Page

_HEADING = re.compile(r"^(\d+(\.\d+)*\.?|[A-ZÁÉÍÓÚÑ]{2,}[A-ZÁÉÍÓÚÑ ]*|[a-z]\))\s")
# a numbered heading: "4.", "4.2", "CLÁUSULA 4." then an UPPER-CASE title ("SOLVENCIA TÉCNICA");
# "1. El objeto..." (a numbered sentence) is not one. Or an annex: "ANEXO I", "ANEXO II. ..."
_UPPER = "A-ZÁÉÍÓÚÑÜ"
_NUMBERED = re.compile(
    rf"^(?:CL[AÁ]USULA\s+)?(\d+(?:\.\d+)*)\.?\s+([{_UPPER}][{_UPPER} ,/()-]*[{_UPPER})])(?=\s|$)"
)
_ANNEX = re.compile(
    rf"^(ANEXO\s+[IVXLC\d]+)\b\.?\s*([{_UPPER}][{_UPPER} ,/()-]*[{_UPPER})](?=\s|$))?"
)
_SENTENCE_END = re.compile(r"(?<=[.;:])\s+(?=[A-ZÁÉÍÓÚÑ0-9])")


@dataclass(frozen=True)
class Chunk:
    id: str
    doc: str
    page: int
    text: str
    section: str = ""  # heading path in force where the chunk starts; "" before the first

    @property
    def indexed(self) -> str:
        """What retrieval ranks: the section, then the text."""
        return f"{self.section}\n{self.text}" if self.section else self.text

    def render(self) -> str:
        """How a model reads it: id and page to cite, the section as context, then the text."""
        section = f"Sección: {self.section}\n" if self.section else ""
        return f"[{self.id}] ({self.doc}, pág. {self.page})\n{section}{self.text}"


def heading(paragraph: str) -> tuple[int, str] | None:
    """(depth, title) if the paragraph opens a section: "4.2 SOLVENCIA TÉCNICA ..." -> (2, ...)."""
    if m := _NUMBERED.match(paragraph):
        number, title = m.groups()
        if sum(c.isalpha() for c in title) >= 4:
            return number.count(".") + 1, f"{number}. {title.strip()}"[:80]
    if m := _ANNEX.match(paragraph):  # depth 0: numbered sections inside an annex nest under it
        return 0, " ".join(x for x in m.groups() if x)[:80]
    return None


def paragraphs(text: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if current:
                out.append(" ".join(current))
                current = []
            continue
        if (
            current
            and current[-1].endswith((".", ":", ";"))
            and (_HEADING.match(line) or line[:1].isupper())
        ):
            out.append(" ".join(current))
            current = []
        current.append(line)
    if current:
        out.append(" ".join(current))
    return [re.sub(r"(\w)- (\w)", r"\1\2", p) for p in out]  # re-join hyphenated words


def _pieces(paragraph: str, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        if current and len(current) + 1 + len(sentence) > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
        while len(current) > max_chars:  # one sentence longer than a chunk: hard cut
            pieces.append(current[:max_chars])
            current = current[max_chars:]
    if current:
        pieces.append(current)
    return pieces


def chunk_pages(
    doc: str, pages: Iterable[Page], max_chars: int = 1200, overlap: int = 1
) -> list[Chunk]:
    chunks: list[Chunk] = []
    path: dict[int, str] = {}  # depth -> title, carried across pages
    for page in pages:
        if page.scanned:
            continue
        parts: list[str] = []
        sections: list[str] = []  # the heading path after each part
        for p in paragraphs(page.text):
            if h := heading(p):
                depth, title = h
                path = {d: t for d, t in path.items() if d < depth} | {depth: title}
            for piece in _pieces(p, max_chars):
                parts.append(piece)
                sections.append(" > ".join(path[d] for d in sorted(path)))
        start, n = 0, 0
        while start < len(parts):
            end, size = start, 0
            while end < len(parts) and (end == start or size + len(parts[end]) <= max_chars):
                size += len(parts[end]) + 1
                end += 1
            text = "\n".join(parts[start:end])
            chunks.append(
                Chunk(f"{doc}:p{page.number}:{n}", doc, page.number, text, sections[start])
            )
            n += 1
            if end >= len(parts):
                break
            start = max(end - overlap, start + 1)
    return chunks
