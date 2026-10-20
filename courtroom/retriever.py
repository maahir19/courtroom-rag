"""Hybrid retriever: BM25 (keyword) + TF-IDF cosine (soft-ish matching).

Why not embeddings? I tried sentence-transformers first. It worked, but it's a 400MB
download, takes forever on my laptop and doesn't run on the lab PCs. For docs like
policies where exact words ("14 days", "intern", "encash") matter, BM25 was actually
better in my tests. TF-IDF with bigrams fills some of the gap. See notes/dev_log.md.
"""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from . import config
from .ingest import Chunk
from .text_utils import tokenize, cue_scores


class BM25:
    """Okapi BM25, written from the formula on Wikipedia. k1/b are the usual defaults."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = docs
        self.N = len(docs)
        self.avgdl = sum(len(d) for d in docs) / max(self.N, 1)
        self.tf = [Counter(d) for d in docs]
        df = Counter()
        for d in docs:
            df.update(set(d))
        self.idf = {t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(self.N)
        for i, tf in enumerate(self.tf):
            dl = len(self.docs[i])
            s = 0.0
            for q in query:
                if q not in tf:
                    continue
                f = tf[q]
                s += self.idf.get(q, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            out[i] = s
        return out


def _minmax(x: np.ndarray) -> np.ndarray:
    if x.max() - x.min() < 1e-9:
        return np.zeros_like(x)
    return (x - x.min()) / (x.max() - x.min())


class HybridRetriever:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.by_id = {c.id: c for c in chunks}
        # include title + section in what we index; helps a LOT for "intern" style queries
        texts = [f"{c.title} {c.section} {c.text}" for c in chunks]
        self.tokens = [tokenize(t) for t in texts]
        self.bm25 = BM25(self.tokens)
        self.tfidf = TfidfVectorizer(tokenizer=tokenize, lowercase=False, ngram_range=(1, 2),
                                     sublinear_tf=True, token_pattern=None)
        self.matrix = self.tfidf.fit_transform(texts) if texts else None
        self.cues = [cue_scores(c.text) for c in chunks]

    def relevance(self, query: str) -> np.ndarray:
        if not self.chunks:
            return np.zeros(0)
        q_tokens = tokenize(query)
        bm = _minmax(self.bm25.scores(q_tokens))
        qv = self.tfidf.transform([query])
        cos = (self.matrix @ qv.T).toarray().ravel()
        cos = _minmax(cos)
        return config.BM25_WEIGHT * bm + (1 - config.BM25_WEIGHT) * cos

    def search(self, query: str, side: str | None = None, k: int = config.TOP_K,
               exclude: set[str] | None = None) -> list[dict]:
        """side = 'for' | 'against' | None.

        The stance bias is small on purpose (max ~25%). If it's bigger, the defense
        starts pulling random paragraphs that just happen to say "not" a lot.
        """
        rel = self.relevance(query)
        if rel.size == 0:
            return []
        final = rel.copy()
        if side:
            for i, (sup, opp) in enumerate(self.cues):
                lean = sup - opp if side == "for" else opp - sup
                final[i] *= 1 + 0.25 * math.tanh(lean / 4)

        order = np.argsort(-final)
        picked, per_doc = [], Counter()
        for i in order:
            c = self.chunks[i]
            if rel[i] < 0.08:  # basically unrelated, stop
                break
            if exclude and c.id in exclude:
                continue
            if per_doc[c.doc] >= config.MAX_PER_DOC:
                continue
            per_doc[c.doc] += 1
            picked.append({**c.to_dict(), "score": round(float(final[i]), 4),
                           "relevance": round(float(rel[i]), 4)})
            if len(picked) >= k:
                break
        return picked
