"""Dense retriever (SPEC section 4, D5, D7, D9). Replaces the S0 seam; the interface is unchanged:
`Retriever.ingest(docs)` and `Retriever.search(query, filters=...) -> list[Source]`. Chunk id is `<doc_id>#<index>`.

Search embeds the query, takes the top `top_k` chunks by cosine and keeps those at or above `min_score`; an
empty result means the best chunk is below `min_score` (or the index is empty) and the caller abstains.
"""

from __future__ import annotations

from typing import Any

from milo.rag.chunk import DEFAULT_OVERLAP, DEFAULT_SIZE, chunk_document
from milo.rag.vectors import VectorStore
from milo.types import Document, Embeddings, Source

TOP_K = 5
EMBED_BATCH = 64


class Retriever:
    def __init__(
        self,
        embeddings: Embeddings,
        min_score: float = 0.30,
        top_k: int = TOP_K,
        *,
        chunk_size: int = DEFAULT_SIZE,
        overlap: int = DEFAULT_OVERLAP,
        store: VectorStore | None = None,
    ) -> None:
        self.embeddings = embeddings
        self.min_score = min_score
        self.top_k = top_k
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.store = store if store is not None else VectorStore()

    def count(self) -> int:
        return self.store.count()

    def ingest(self, docs: list[Document]) -> None:
        chunks = [
            c
            for d in docs
            for c in chunk_document(
                d.doc_id, d.text, d.metadata, self.chunk_size, self.overlap
            )
        ]
        if not chunks:
            return
        vectors: list[list[float]] = []
        for i in range(0, len(chunks), EMBED_BATCH):
            vectors += self.embeddings.embed(
                [c.text for c in chunks[i : i + EMBED_BATCH]]
            )
        self.store.add(chunks, vectors)

    def search(
        self, query: str, *, filters: dict[str, Any] | None = None
    ) -> list[Source]:
        """Top chunks by cosine at or above `min_score`. Empty index: no embedding call, empty result."""
        if self.store.count() == 0:
            return []
        qvec = self.embeddings.embed([query])[0]
        hits = self.store.search(qvec, self.top_k, filters)
        return [
            Source(h.chunk_id, h.doc_id, h.score, h.text)
            for h in hits
            if h.score >= self.min_score
        ]
