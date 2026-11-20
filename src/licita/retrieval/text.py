"""Text normalisation shared by BM25 and the quote check.

Pliegos write the same word as "solvencia" / "Solvencia" / "SOLVENCIA", with or without accents
("económica" vs "economica" in OCR'd or badly encoded PDFs), and PDF text breaks lines anywhere.
"""

from __future__ import annotations

import re
import unicodedata

# the commonest Spanish function words: they match everywhere and rank nothing
_STOPWORDS = (
    "a al ante bajo con contra de del desde durante e el en entre es esta este hacia hasta la "
    "las lo los mediante o para por que se segun sin sobre su sus tras u un una unas unos y"
)
STOPWORDS = frozenset(_STOPWORDS.split())


def fold(text: str) -> str:
    """Lowercase, no accents (ñ kept as n), whitespace collapsed."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plain).strip()


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"\w+", fold(text)) if t not in STOPWORDS]
