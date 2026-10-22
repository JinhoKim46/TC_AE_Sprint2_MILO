"""Vector store on Chroma (SPEC D6): cosine space, `embeddings=` always passed, telemetry off.

Chroma holds the chunks, vectors and metadata, in memory (`path=None`, a fresh uuid collection) or on disk
under `data/index/<name>/`. Search is an exact cosine scan of the stored vectors in float64: with a few
hundred chunks it costs milliseconds, and it has no approximate-index effects, so a ranking is reproducible
(HARNESS 7.7). Filters on `section`, `product` and `updated_after` are applied by Chroma before scoring.
"""

from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from milo.rag.chunk import Chunk

MANIFEST = "index.json"
COLLECTION = "chunks"
_ADD_BATCH = 500


@dataclass
class Hit:
    chunk_id: str
    doc_id: str
    score: float
    text: str
    metadata: dict[str, Any]


def _clause(key: str, value: Any) -> dict[str, Any]:
    if key == "updated_after":
        num = int(value) if isinstance(value, int) else int(str(value).replace("-", ""))
        return {"updated_num": {"$gt": num}}
    if isinstance(value, (list, tuple, set)):
        return {key: {"$in": sorted(value)}}
    return {key: value}


def build_where(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    """Chroma `where` for `{section, product, updated, updated_after, ...}`; a missing key never matches."""
    clauses = [_clause(k, v) for k, v in (filters or {}).items() if v is not None]
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _cosine(a: list[float], b: list[float], na: float, nb: float) -> float:
    if not na or not nb:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)


class VectorStore:
    def __init__(self, path: Path | str | None = None) -> None:
        os.environ.setdefault(
            "ANONYMIZED_TELEMETRY", "False"
        )  # before the import (SPEC D6)
        import chromadb  # lazy: `import milo.app` must load no vector database (SPEC 11.12)

        self.path = Path(path) if path is not None else None
        if self.path is None:
            self._client: Any = chromadb.EphemeralClient()
            name = f"milo_{uuid.uuid4().hex}"
        else:
            self.path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.path / "chroma"))
            name = COLLECTION
        self._col = self._client.get_or_create_collection(
            name, metadata={"hnsw:space": "cosine"}
        )
        self._cache: dict[str, list[tuple[Hit, list[float], float]]] = {}

    def count(self) -> int:
        return int(self._col.count())

    def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Upsert chunks; any older chunks of the same documents are removed first."""
        if len(chunks) != len(vectors):
            raise ValueError("one vector per chunk")
        for doc_id in sorted({c.doc_id for c in chunks}):
            self._col.delete(where={"doc_id": doc_id})
        for i in range(0, len(chunks), _ADD_BATCH):
            part = chunks[i : i + _ADD_BATCH]
            self._col.upsert(
                ids=[c.chunk_id for c in part],
                embeddings=vectors[i : i + _ADD_BATCH],
                documents=[c.text for c in part],
                metadatas=[
                    {**c.metadata, "doc_id": c.doc_id, "chunk_index": c.index}
                    for c in part
                ],
            )
        self._cache.clear()

    def _rows(
        self, filters: dict[str, Any] | None
    ) -> list[tuple[Hit, list[float], float]]:
        key = json.dumps(filters or {}, sort_keys=True, default=str)
        if key not in self._cache:
            got = self._col.get(
                where=build_where(filters),
                include=["embeddings", "documents", "metadatas"],
            )
            embeddings = got["embeddings"] if got["embeddings"] is not None else []
            rows = []
            for cid, vec, doc, meta in zip(
                got["ids"],
                embeddings,
                got["documents"] or [],
                got["metadatas"] or [],
                strict=True,
            ):
                v = [float(x) for x in vec]
                m = dict(meta)
                rows.append(
                    (
                        Hit(cid, str(m["doc_id"]), 0.0, doc, m),
                        v,
                        math.sqrt(sum(x * x for x in v)),
                    )
                )
            self._cache[key] = rows
        return self._cache[key]

    def rank(
        self, query_vec: list[float], filters: dict[str, Any] | None = None
    ) -> list[Hit]:
        """Every chunk that passes the filters, best cosine first (ties by document and chunk index)."""
        qn = math.sqrt(sum(x * x for x in query_vec))
        hits = [
            Hit(h.chunk_id, h.doc_id, _cosine(query_vec, v, qn, n), h.text, h.metadata)
            for h, v, n in self._rows(filters)
        ]
        hits.sort(key=lambda h: (-h.score, h.doc_id, int(h.metadata["chunk_index"])))
        return hits

    def search(
        self,
        query_vec: list[float],
        k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[Hit]:
        return self.rank(query_vec, filters)[:k]

    # ---- the manifest next to an on-disk index ----

    def write_manifest(self, info: dict[str, Any]) -> None:
        if self.path is not None:
            (self.path / MANIFEST).write_text(
                json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

    def read_manifest(self) -> dict[str, Any] | None:
        if self.path is None or not (self.path / MANIFEST).exists():
            return None
        data = json.loads((self.path / MANIFEST).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
