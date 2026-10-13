"""Local vector store: FAISS (if installed and selected) or a NumPy exact-search fallback."""
from __future__ import annotations

import numpy as np


class VectorStore:
    def __init__(self, dim: int, backend: str = "numpy"):
        self.dim = dim
        self.backend_requested = backend
        self.backend = "numpy"
        self.matrix = np.zeros((0, dim), dtype=np.float32)
        self.index = None
        self.note = None
        if backend == "faiss":
            try:
                import faiss  # optional dependency
                self.index = faiss.IndexFlatIP(dim)
                self.backend = "faiss"
            except Exception as exc:  # pragma: no cover - optional dependency
                self.note = f"FAISS unavailable ({exc.__class__.__name__}); using NumPy exact search"

    def add(self, vectors: np.ndarray) -> None:
        vectors = np.ascontiguousarray(vectors, dtype=np.float32)
        self.matrix = np.vstack([self.matrix, vectors]) if len(self.matrix) else vectors
        if self.index is not None:
            self.index.add(vectors)

    def __len__(self) -> int:
        return len(self.matrix)

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        if not len(self.matrix):
            return []
        k = min(k, len(self.matrix))
        q = np.ascontiguousarray(query.reshape(1, -1), dtype=np.float32)
        if self.index is not None:
            scores, ids = self.index.search(q, k)
            return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i >= 0]
        scores = (self.matrix @ q[0])
        order = np.argsort(-scores)[:k]
        return [(int(i), float(scores[i])) for i in order]
