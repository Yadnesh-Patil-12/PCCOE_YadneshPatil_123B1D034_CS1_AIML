"""Hybrid retrieval over one project: BM25 (lexical) + dense vectors, fused with configurable weights."""
from __future__ import annotations

import math
from collections import Counter

import numpy as np

from .embeddings import make_embedder
from .textutil import query_terms, tokens
from .vectorstore import VectorStore

K1, B = 1.5, 0.75


class HybridIndex:
    def __init__(self, cfg: dict, chunks: list[dict]):
        """chunks: dicts with keys doc_id, version, sec_id, sec_title, text (injection-flagged chunks are excluded)."""
        self.cfg = cfg
        self.chunks = chunks
        self.notes: list[str] = []
        self.header = [f"{c['doc_id']} section {c['sec_id']} {c['sec_title']}" for c in chunks]
        self.texts = [h + "\n" + c["text"] for h, c in zip(self.header, chunks)]
        self.tok = [tokens(t) for t in self.texts]
        self.tf = [Counter(t) for t in self.tok]
        self.dl = np.array([len(t) for t in self.tok], dtype=np.float32)
        self.avgdl = float(self.dl.mean()) if len(self.dl) else 1.0
        df: Counter = Counter()
        for tf in self.tf:
            df.update(tf.keys())
        n = max(len(chunks), 1)
        self.n = n
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self.vocab = set(df)
        self.embedder, note = make_embedder(cfg)
        if note:
            self.notes.append(note)
        self.embedder.fit(self.texts)
        self.store = VectorStore(self.embedder.dim, cfg["vector_store"]["backend"])
        if self.store.note:
            self.notes.append(self.store.note)
        if self.texts:
            self.store.add(self.embedder.embed(self.texts))

    # ------------------------------------------------------------------ #
    def bm25(self, terms: list[str]) -> np.ndarray:
        scores = np.zeros(len(self.chunks), dtype=np.float32)
        for t in terms:
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(t, 0)
                if f:
                    scores[i] += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * self.dl[i] / self.avgdl))
        return scores

    def max_bm25(self, terms: list[str]) -> float:
        return sum(self.idf.get(t, 0.0) * (K1 + 1) for t in terms) or 1.0

    def search(self, question: str, k: int | None = None) -> dict:
        cfgr = self.cfg["retrieval"]
        k = k or cfgr["top_k"]
        terms = query_terms(question)
        if not self.chunks:
            return dict(hits=[], terms=terms, coverage=0.0, vocab_cov=0.0, bm25_abs=0.0, dense_abs=0.0)
        bm = self.bm25(terms)
        dense_pairs = self.store.search(self.embedder.embed([question])[0], len(self.chunks))
        dn = np.zeros(len(self.chunks), dtype=np.float32)
        for i, s in dense_pairs:
            dn[i] = s
        bm_n = bm / (bm.max() or 1.0)
        dn_n = np.clip(dn, 0, None) / (np.clip(dn, 0, None).max() or 1.0)
        fused = cfgr["weight_bm25"] * bm_n + cfgr["weight_dense"] * dn_n
        order = np.argsort(-fused)[:k]
        hits = [dict(idx=int(i), fused=float(fused[i]), bm25=float(bm[i]), dense=float(dn[i])) for i in order]
        # confidence ingredients
        in_vocab = [t for t in terms if t in self.vocab]
        vocab_cov = len(in_vocab) / len(terms) if terms else 0.0
        top_tokens = set()
        for h in hits[:3]:
            top_tokens |= set(self.tok[h["idx"]])
        mass = sum(self.idf.get(t, 1.0) for t in terms) or 1.0
        coverage = sum(self.idf.get(t, 1.0) for t in terms if t in top_tokens) / mass
        return dict(hits=hits, terms=terms, coverage=coverage, vocab_cov=vocab_cov,
                    bm25_abs=float(bm[order[0]]) / self.max_bm25(terms), dense_abs=float(dn[order[0]]))
