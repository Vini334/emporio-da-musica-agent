"""Hybrid top-k search over the policies collection (SPEC §7).

Dense (fastembed + Chroma) and lexical (BM25) rankings are fused with Reciprocal Rank Fusion:
the embeddings catch paraphrases ("me arrependi" → direito de arrependimento) and BM25 catches
the store's exact vocabulary ("boquilha", "parcelar" → parcelamento) that the small
multilingual model misses. The manual is small, so both rankings cover every chunk.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from chromadb.errors import NotFoundError

from emporio import config
from emporio.rag import ingest
from emporio.rag.lexical import BM25

DEFAULT_K = 3
RRF_K = 10  # small constant: with ~25 chunks the top ranks should dominate


@dataclass(frozen=True)
class RetrievedChunk:
    section: str
    title: str
    parent_title: str
    text: str
    score: float  # fused RRF score (only meaningful for ordering)


class PolicyRetriever:
    """Builds the index on first use if it is missing or stale, then queries it."""

    def __init__(
        self,
        pdf_path: Path = config.POLICIES_PDF,
        chroma_dir: Path = config.CHROMA_DIR,
        model_name: str = config.EMBEDDING_MODEL,
    ) -> None:
        self.pdf_path, self.chroma_dir, self.model_name = pdf_path, chroma_dir, model_name
        self._load()

    def _load(self) -> None:
        ingest.ensure_index(self.pdf_path, self.chroma_dir, self.model_name)
        self._collection = ingest.chroma_client(self.chroma_dir).get_collection(ingest.COLLECTION)
        stored = self._collection.get(include=["documents", "metadatas"])
        self._chunks = {
            chunk_id: RetrievedChunk(
                section=str(meta["section"]),
                title=str(meta["title"]),
                parent_title=str(meta.get("parent_title") or ""),
                text=document,
                score=0.0,
            )
            for chunk_id, document, meta in zip(
                stored["ids"], stored["documents"], stored["metadatas"], strict=True
            )
        }
        self._ids = list(self._chunks)
        self._bm25 = BM25(
            [f"{c.parent_title} {c.section} {c.title}\n{c.text}" for c in self._chunks.values()]
        )

    def dense_ranking(self, question: str) -> list[str]:
        vector = ingest.embed([question], self.model_name)
        try:
            result = self._collection.query(
                query_embeddings=vector, n_results=len(self._ids), include=[]
            )
        except NotFoundError:  # index rebuilt (ingest --force) while this process was running
            self._load()
            result = self._collection.query(
                query_embeddings=vector, n_results=len(self._ids), include=[]
            )
        return result["ids"][0]

    def lexical_ranking(self, question: str) -> list[str]:
        scores = self._bm25.scores(question)
        ranked = sorted(zip(self._ids, scores, strict=True), key=lambda pair: -pair[1])
        return [chunk_id for chunk_id, score in ranked if score > 0]

    def search(self, question: str, k: int = DEFAULT_K) -> list[RetrievedChunk]:
        question = question.strip()
        if not question:
            return []
        fused: dict[str, float] = {}
        for ranking in (self.dense_ranking(question), self.lexical_ranking(question)):
            for rank, chunk_id in enumerate(ranking):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1 / (RRF_K + rank + 1)
        best = sorted(fused.items(), key=lambda pair: -pair[1])[:k]
        return [
            RetrievedChunk(**{**self._chunks[chunk_id].__dict__, "score": round(score, 4)})
            for chunk_id, score in best
        ]


@lru_cache(maxsize=1)
def get_retriever() -> PolicyRetriever:
    return PolicyRetriever()
