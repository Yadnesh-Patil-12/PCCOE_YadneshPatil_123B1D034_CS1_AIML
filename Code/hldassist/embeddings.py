"""Embedding back-ends.

* HashingEmbedder (default): fully offline TF-IDF style hashed vectors (no download, deterministic).
* BgeEmbedder (optional): sentence-transformers model, e.g. BAAI/bge-small-en-v1.5 (needs internet once to
  download the weights, then runs locally).
"""
from __future__ import annotations

import math
import zlib
from collections import Counter

import numpy as np

from .textutil import tokens


class HashingEmbedder:
    name = "hashing-tfidf"

    def __init__(self, dim: int = 2048):
        self.dim = dim
        self.idf = np.ones(dim, dtype=np.float32)

    @staticmethod
    def _h(tok: str) -> int:
        return zlib.crc32(tok.encode("utf-8"))

    def _features(self, text: str) -> Counter:
        toks = tokens(text)
        feats = Counter(toks)
        for a, b in zip(toks, toks[1:]):
            feats[a + " " + b] += 1
        return feats

    def fit(self, texts: list[str]) -> None:
        df = np.zeros(self.dim, dtype=np.float32)
        for t in texts:
            seen = {self._h(f) % self.dim for f in self._features(t)}
            for i in seen:
                df[i] += 1
        n = max(len(texts), 1)
        self.idf = (np.log((1 + n) / (1 + df)) + 1.0).astype(np.float32)

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for r, t in enumerate(texts):
            for f, c in self._features(t).items():
                h = self._h(f)
                idx = h % self.dim
                sign = 1.0 if (h >> 31) & 1 else -1.0
                # signed hashing keeps collisions unbiased; sub-linear tf damps repeated terms
                out[r, idx] += sign * (1.0 + math.log(c)) * self.idf[idx]
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


class BgeEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # optional dependency
        self.model = SentenceTransformer(model_name)
        self.name = model_name
        self.dim = self.model.get_sentence_embedding_dimension()

    def fit(self, texts):  # nothing to fit
        return None

    def embed(self, texts: list[str]) -> np.ndarray:
        v = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(v, dtype=np.float32)


def make_embedder(cfg: dict):
    prov = cfg["embedding"]["provider"].lower()
    if prov == "bge":
        try:
            return BgeEmbedder(cfg["embedding"]["bge_model"]), None
        except Exception as exc:  # pragma: no cover - depends on optional install
            return HashingEmbedder(cfg["embedding"]["dim"]), f"BGE unavailable ({exc.__class__.__name__}); using offline hashing embeddings"
    return HashingEmbedder(cfg["embedding"]["dim"]), None
