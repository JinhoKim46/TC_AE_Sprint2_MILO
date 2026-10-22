"""Retriever seam (SPEC section 4, 17). S0 implementation: an in-process cosine index with no vector database.

M1 replaces the body (chunking in `chunk.py`, the Chroma store in `vectors.py`). The interface stays:
`Retriever.ingest(docs)` and `Retriever.search(query, ...) -> list[Source]`. Chunk id is `<doc_id>#<index>`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from milo.types import Document, Embeddings, Source

MAX_CHUNK_CHARS = 800
CHUNK_OVERLAP = 100
TOP_K = 5

_HEADING = re.compile(r"^#{1,6}\s")


@dataclass
class _Chunk:
    chunk_id: str
    doc_id: str
    text: str
    metadata: dict[str, Any]
    vector: list[float]


def split_text(
    text: str, max_chars: int = MAX_CHUNK_CHARS, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Heading-aware split: paragraphs are packed up to `max_chars`; a heading starts a new chunk."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if current and (
            _HEADING.match(para) or len(current) + 2 + len(para) > max_chars
        ):
            chunks.append(current)
            tail = current[-overlap:] if overlap and not _HEADING.match(para) else ""
            current = (tail + "\n\n" + para).strip() if tail else para
        else:
            current = f"{current}\n\n{para}" if current else para
        while len(current) > max_chars:
            chunks.append(current[:max_chars])
            current = current[max_chars - overlap :]
    if current:
        chunks.append(current)
    return chunks


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Retriever:
    def __init__(
        self, embeddings: Embeddings, min_score: float = 0.30, top_k: int = TOP_K
    ) -> None:
        self.embeddings = embeddings
        self.min_score = min_score
        self.top_k = top_k
        self._chunks: list[_Chunk] = []

    def count(self) -> int:
        return len(self._chunks)

    def ingest(self, docs: list[Document]) -> None:
        texts: list[str] = []
        owners: list[tuple[str, int, dict[str, Any]]] = []
        for doc in docs:
            for i, chunk in enumerate(split_text(doc.text)):
                texts.append(chunk)
                owners.append((doc.doc_id, i, dict(doc.metadata)))
        if not texts:
            return
        vectors = self.embeddings.embed(texts)
        for text, (doc_id, i, meta), vec in zip(texts, owners, vectors, strict=True):
            self._chunks.append(_Chunk(f"{doc_id}#{i}", doc_id, text, meta, vec))

    def search(
        self, query: str, *, filters: dict[str, Any] | None = None
    ) -> list[Source]:
        """Top chunks by cosine at or above `min_score`. Empty index: no embedding call, empty result."""
        if not self._chunks:
            return []
        qvec = self.embeddings.embed([query])[0]
        scored = []
        for ch in self._chunks:
            if filters and any(ch.metadata.get(k) != v for k, v in filters.items()):
                continue
            score = _cosine(qvec, ch.vector)
            if score >= self.min_score:
                scored.append(Source(ch.chunk_id, ch.doc_id, score, ch.text))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[: self.top_k]
