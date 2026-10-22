"""BM25 over the same chunks as the dense index (SPEC D8). `rank_bm25.BM25Okapi`, lower-cased alphanumeric tokens.

`rank(query, filters)` returns the chunk ids that share at least one token with the query, best score first
(ties by chunk order), so a chunk with no lexical overlap never enters a fusion list. Filters are the same keys
as the vector store: `section`, `product` (equality or a list of values) and `updated_after`.
"""

from __future__ import annotations

import re
from typing import Any

from rank_bm25 import BM25Okapi

from milo.rag.chunk import Chunk

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _passes(meta: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    for key, value in (filters or {}).items():
        if value is None:
            continue
        if key == "updated_after":
            num = (
                int(value)
                if isinstance(value, int)
                else int(str(value).replace("-", ""))
            )
            if int(meta.get("updated_num", 0)) <= num:
                return False
        elif isinstance(value, (list, tuple, set)):
            if meta.get(key) not in value:
                return False
        elif meta.get(key) != value:
            return False
    return True


class BM25Index:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = list(chunks)
        self._tokens = [tokenize(c.text) for c in self.chunks]
        self._bm25 = BM25Okapi(self._tokens) if self.chunks else None

    def rank(
        self, query: str, filters: dict[str, Any] | None = None
    ) -> list[tuple[str, float]]:
        q = tokenize(query)
        if self._bm25 is None or not q:
            return []
        scores = self._bm25.get_scores(q)
        qset = set(q)
        hits = [
            (i, float(scores[i]))
            for i, c in enumerate(self.chunks)
            if qset & set(self._tokens[i]) and _passes(c.metadata, filters)
        ]
        hits.sort(key=lambda h: (-h[1], h[0]))
        return [(self.chunks[i].chunk_id, s) for i, s in hits]
