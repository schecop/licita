"""Hybrid retrieval over one tender's chunks: BM25 and dense, fused by reciprocal rank.

    rrf(d) = sum over rankers r of 1 / (k + rank_r(d))      (k = 60, Cormack et al. 2009)

Why rank fusion and not a weighted sum of scores: BM25 scores are unbounded and cosine scores
live in [-1, 1], so any weight between them is arbitrary and shifts with the query; ranks are
comparable as they are. Each hit keeps both ranks, so the trace shows which ranker found it.

A tender has a few hundred chunks: everything lives in memory (numpy), no vector database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from licita.documents.chunk import Chunk
from licita.retrieval.bm25 import BM25
from licita.retrieval.embed import Embedder, Vectors

RRF_K = 60


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float  # fused
    bm25_rank: int | None  # 1-based; None: no query term in the chunk
    dense_rank: int


class HybridIndex:
    def __init__(self, chunks: Sequence[Chunk], vectors: Vectors, embedder: Embedder) -> None:
        self.chunks = list(chunks)
        self.vectors = vectors
        self.embedder = embedder
        self.bm25 = BM25([c.indexed for c in self.chunks])

    @classmethod
    async def build(cls, chunks: Sequence[Chunk], embedder: Embedder) -> HybridIndex:
        vectors = await embedder.embed([c.indexed for c in chunks]) if chunks else np.zeros((0, 1))
        return cls(chunks, vectors.astype(np.float32), embedder)

    async def search(self, query: str, k: int = 8) -> list[Hit]:
        if not self.chunks:
            return []
        lexical = self.bm25.scores(query)
        q = await self.embedder.embed([query])
        dense = (self.vectors @ q[0]).tolist()
        by_bm25 = sorted(
            (i for i in range(len(lexical)) if lexical[i] > 0), key=lambda i: -lexical[i]
        )
        by_dense = sorted(range(len(dense)), key=lambda i: -dense[i])
        bm25_rank = {i: r + 1 for r, i in enumerate(by_bm25)}
        dense_rank = {i: r + 1 for r, i in enumerate(by_dense)}
        fused = {
            i: 1 / (RRF_K + dense_rank[i]) + (1 / (RRF_K + bm25_rank[i]) if i in bm25_rank else 0)
            for i in range(len(self.chunks))
        }
        best = sorted(fused, key=lambda i: -fused[i])[:k]
        return [Hit(self.chunks[i], fused[i], bm25_rank.get(i), dense_rank[i]) for i in best]
