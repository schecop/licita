"""Pages -> chunks a retriever can rank and a model can cite.

Rules that keep citations honest:
- A chunk never crosses a page: its page number is the citation's page.
- Chunks are built from whole paragraphs, up to `max_chars`; a paragraph longer than that is cut
  at sentence ends. Neighbouring chunks share one paragraph (`overlap`), so a clause split
  across a paragraph boundary is whole in at least one chunk.
- The id says where the chunk is (`<doc>:p<page>:<n>`), so a trace or a review screen can show
  the source without a lookup.
- Each chunk carries the section it falls under ("8. Capacidad para contratar > 8.2. Solvencia
  económica"), carried across pages. A long clause spans several chunks and only the first holds
  its heading; without it, "por importe igual o superior a 300.000 euros" in the third chunk of
  the solvency clause would not say it is solvency, and the solvency query would miss it. The
  section is indexed with the text and shown to the model, but it is not part of `text`: quotes
  are checked against `text` only, so a citation never lands on a heading printed pages earlier.

Clean-up before chunking, from the first real pliegos:
- Lines repeated on most pages (a signature stamp "Docusign Envelope ID: ...", a running header,
  "Página 3 de 71") are dropped: they say nothing and break paragraphs on every page.
- Table-of-contents lines ("11. Criterios de adjudicación ........ 25") are dropped: they would
  set a heading pages before its clause, and they match every query.

A heading is a short line on its own: a clause number and a title in any case ("11. Criterios de
adjudicación", "4.2 SOLVENCIA TÉCNICA", "CLÁUSULA 8."), a Roman-numbered part ("III. DE LA
LICITACIÓN") or an annex ("ANEXO II"). Not a heading: a table row (an amount or a percentage), a
numbered sentence that runs on, a year. PDF text breaks lines at the layout width; other lines
are re-joined into paragraphs.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from licita.documents.pdf import Page

_HEADING_MAX = 80  # a heading is a short line; a run-on numbered sentence is longer
_NUMBERED = re.compile(r"^(?i:CL[AÁ]USULA\s+)?(\d{1,2}(?:\.\d{1,2})*)\.?\s+([A-ZÁÉÍÓÚÑ¿].*)$")
# "CLAUSULA SEGUNDA. Órgano de contratación.", "CLÁUSULA VIGÉSIMO PRIMERA.- Penalidades": the
# keyword makes it a heading even when the title is long
_ORDINAL = re.compile(
    r"^((?i:CL[AÁ]USULA|ART[IÍ]CULO))\s+([A-ZÁÉÍÓÚ]{4,}(?:[\s-][A-ZÁÉÍÓÚ]{4,}){0,2})"
    r"\s*[.:\-]+\s*(.+)$"
)
_ORDINAL_MAX = 140
_ROMAN = re.compile(r"^(?:T[IÍ]TULO\s+|CAP[IÍ]TULO\s+)?([IVXL]{1,5})\.\s+([A-ZÁÉÍÓÚÑ].*)$")
# the cuadro de características: "F. Solvencia", "F.1. Clasificación empresarial"
_LETTER = re.compile(r"^([A-Z])(?:\.(\d{1,2}))?\.\s+([A-ZÁÉÍÓÚÑ].*)$")
_ANNEX = re.compile(r"^(ANEXO\s+[IVXLC\d]+)\b[.:\-\N{EN DASH}]?\s*(.*)$", re.IGNORECASE)
_TOC_LINE = re.compile(r"(\.\s?){5,}")  # dot leaders ("Recursos ........ 54")
_NOT_A_TITLE = re.compile(r"[€%]|\d{1,3}(\.\d{3})+,\d{2}")  # amounts and percentages: a table
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


def _title(text: str) -> bool:
    text = text.strip()
    return (
        sum(c.isalpha() for c in text) >= 4
        and not _NOT_A_TITLE.search(text)
        and not text.endswith((",", ";"))
    )


def heading(line: str) -> tuple[int, str] | None:
    """(depth, title) if the line opens a section: "4.2 Solvencia técnica" -> (2, "4.2. ...").

    Depth 0 is a part or an annex: numbered clauses nest under it."""
    line = " ".join(line.split())
    if len(line) <= _ORDINAL_MAX and (m := _ORDINAL.match(line)) and _title(m[3]):
        return 1, f"{m[1].capitalize()} {m[2].lower()}. {m[3]}"
    if len(line) > _HEADING_MAX:
        return None
    if (m := _NUMBERED.match(line)) and _title(m[2]):
        return m[1].count(".") + 1, f"{m[1]}. {m[2]}"
    roman = _ROMAN.match(line)
    # "I. DISPOSICIONES GENERALES" is a part; "I. Criterios de adjudicación" is the cuadro's I
    if roman and _title(roman[2]) and (len(roman[1]) > 1 or roman[2].isupper()):
        return 0, f"{roman[1]}. {roman[2]}"
    if (m := _LETTER.match(line)) and _title(m[3]):
        return (2, f"{m[1]}.{m[2]}. {m[3]}") if m[2] else (1, f"{m[1]}. {m[3]}")
    # "ANEXO VIII - Oferta técnica." in the middle of a clause is a reference to it; the annex
    # itself is titled in capitals ("ANEXO VIII.- OFERTA TÉCNICA") or not at all
    if (m := _ANNEX.match(line)) and (not m[2] or m[2].isupper()):
        return 0, " ".join(x for x in (m[1].upper(), m[2]) if x)
    return None


def _norm(line: str) -> str:
    return re.sub(r"\d+", "#", " ".join(line.split()).lower())


def repeated_lines(pages: Sequence[Page]) -> set[str]:
    """Normalised lines printed on at least half the pages (3 or more): headers, footers,
    signature stamps, page numbers."""
    counts = Counter(
        n for p in pages for n in {_norm(line) for line in p.text.splitlines() if line.strip()}
    )
    floor = max(3, len(pages) // 2)
    return {n for n, c in counts.items() if c >= floor}


def paragraphs(text: str, drop: set[str] | None = None) -> list[str]:
    return [p for p, _ in _paragraphs(text, drop or set())]


def _paragraphs(text: str, drop: set[str]) -> list[tuple[str, bool]]:
    """(paragraph, is it a heading), in order."""
    out: list[tuple[str, bool]] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            out.append((_dehyphen(" ".join(current)), False))
            current.clear()

    raw_lines = [raw.strip() for raw in text.splitlines()]
    if sum(bool(_TOC_LINE.search(line)) for line in raw_lines) >= 3:
        return []  # a table-of-contents page: its entries wrap, and none is the clause itself
    lines = [
        line for line in raw_lines if not (line and (_norm(line) in drop or _TOC_LINE.search(line)))
    ]
    for i, line in enumerate(lines):
        if not line:
            flush()
            continue
        after = lines[i + 1] if i + 1 < len(lines) else ""
        runs_on = after[:1].islower()  # "1. El objeto del contrato es" + "el servicio de..."
        if not runs_on and heading(line):  # a heading is a paragraph of its own
            flush()
            out.append((" ".join(line.split()), True))
            continue
        if current and current[-1].endswith((".", ":", ";")) and line[:1].isupper():
            flush()
        current.append(line)
    flush()
    return out


def _dehyphen(paragraph: str) -> str:
    return re.sub(r"(\w)- (\w)", r"\1\2", paragraph)  # "contra- to" -> "contrato"


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
    pages = [p for p in pages if not p.scanned]
    drop = repeated_lines(pages)
    chunks: list[Chunk] = []
    path: dict[int, str] = {}  # depth -> title, carried across pages
    for page in pages:
        parts: list[str] = []
        sections: list[str] = []  # the heading path after each part
        for p, is_heading in _paragraphs(page.text, drop):
            if is_heading and (h := heading(p)):
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
