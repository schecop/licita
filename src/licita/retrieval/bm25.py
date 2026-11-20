"""BM25 (Okapi), by hand: the whole scoring rule fits on one screen.

score(q, d) = sum over query terms t of
    idf(t) * f(t, d) * (k1 + 1) / (f(t, d) + k1 * (1 - b + b * |d| / avgdl))
idf(t) = ln(1 + (N - n(t) + 0.5) / (n(t) + 0.5))

Lexical retrieval is what finds exact legal wording and numbers ("solvencia económica",
"volumen anual de negocios", "ISO 27001"); embeddings find paraphrases. Hybrid keeps both.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

from licita.retrieval.text import tokens


class BM25:
    def __init__(self, texts: Sequence[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs = [Counter(tokens(t)) for t in texts]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avgdl = (sum(self.lengths) / len(self.docs)) if self.docs else 0.0
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: str) -> list[float]:
        terms = tokens(query)
        out = []
        for d, length in zip(self.docs, self.lengths, strict=True):
            s = 0.0
            for t in terms:
                f = d.get(t, 0)
                if f:
                    norm = self.k1 * (1 - self.b + self.b * length / (self.avgdl or 1))
                    s += self.idf[t] * f * (self.k1 + 1) / (f + norm)
            out.append(s)
        return out
