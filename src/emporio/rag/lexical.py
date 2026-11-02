"""Small BM25 index for the lexical half of the hybrid search.

The multilingual MiniLM embeddings miss exact store vocabulary ("boquilha", "parcelar" vs
"Parcelamento"). With ~25 chunks an in-memory BM25 costs nothing, and a crude stemmer
(accent-free prefix of 5 letters) is enough to match Portuguese inflections.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from emporio.text import normalize

STEM_LENGTH = 5
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORD_LIST = """
    a ao aos as ate com como da das de do dos e em eu ja la mais me meu minha na nas no nos
    o os ou para pela pelo por pra qual quais quando que se sem ser seu sua tem ter um uma
    voce voces vcs isso esse essa este esta estou foi sao sobre tambem muito posso pode
"""
_STOPWORDS = frozenset(_STOPWORD_LIST.split())


def tokenize(text: str) -> list[str]:
    return [
        token[:STEM_LENGTH]
        for token in _TOKEN.findall(normalize(text))
        if token not in _STOPWORDS and (len(token) > 1 or token.isdigit())
    ]


class BM25:
    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(doc)) for doc in documents]
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.avg_length = sum(self.lengths) / max(len(self.docs), 1)
        df: Counter[str] = Counter()
        for doc in self.docs:
            df.update(doc.keys())
        n = len(self.docs)
        self.idf = {term: math.log(1 + (n - f + 0.5) / (f + 0.5)) for term, f in df.items()}

    def scores(self, query: str) -> list[float]:
        terms = tokenize(query)
        result = []
        for doc, length in zip(self.docs, self.lengths, strict=True):
            norm = self.k1 * (1 - self.b + self.b * length / self.avg_length)
            result.append(
                sum(
                    self.idf[t] * doc[t] * (self.k1 + 1) / (doc[t] + norm)
                    for t in terms
                    if t in doc
                )
            )
        return result
