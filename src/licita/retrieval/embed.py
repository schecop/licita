"""Embeddings behind one small interface, so the model is a config choice (rule 7).

`HashingEmbedder` is not a real model: it hashes word tokens into a fixed-size vector. It is
deterministic, free and offline, so tests and the scripted pipeline run with no key; it behaves
like a bag-of-words, so it must never be the one measured in the retrieval comparison. Real
embedders (text-embedding-3-small, gemini-embedding) arrive with that comparison.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from licita.retrieval.text import tokens

Vectors = NDArray[np.float32]  # one row per text, L2-normalised


class Embedder(Protocol):
    model: str

    async def embed(self, texts: Sequence[str]) -> Vectors: ...


def normalise(m: Vectors) -> Vectors:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return (m / np.where(norms == 0, 1, norms)).astype(np.float32)


class HashingEmbedder:
    def __init__(self, dims: int = 512) -> None:
        self.dims = dims
        self.model = f"hashing-{dims}"

    async def embed(self, texts: Sequence[str]) -> Vectors:
        m = np.zeros((len(texts), self.dims), dtype=np.float32)
        for row, text in enumerate(texts):
            for t in tokens(text):
                h = int.from_bytes(hashlib.blake2b(t.encode(), digest_size=8).digest(), "big")
                m[row, h % self.dims] += 1.0
        return normalise(m)
