"""Ingest the knowledge base (SPEC section 4): `data/kb/**/*.md`, front matter as metadata, body as text.

    python -m milo.rag.ingest data/kb            live: needs MILO_LIVE=1 and OPENROUTER_API_KEY
    python -m milo.rag.ingest data/kb --fake     offline index with the fake embedder (never evidence)

The index goes to `data/index/<model>-<size>-<overlap>/` (git-ignored): Chroma files plus `index.json` (model,
chunk setting, chunk hash, dimension, KB hash). Document id is the path under the KB folder without `.md`.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

from milo.rag.chunk import (
    DEFAULT_OVERLAP,
    DEFAULT_SIZE,
    chunk_document,
    chunks_sha256,
    parse_front_matter,
)
from milo.rag.vectors import VectorStore
from milo.types import Document, Embeddings

INDEX_ROOT = Path(__file__).resolve().parents[3] / "data" / "index"
EMBED_BATCH = 64


def load_kb(kb_dir: Path | str) -> list[Document]:
    root = Path(kb_dir)
    docs = []
    for md in sorted(root.rglob("*.md")):
        meta, body = parse_front_matter(md.read_text(encoding="utf-8"))
        doc_id = md.relative_to(root).with_suffix("").as_posix()
        keep: dict[str, Any] = {
            k: meta[k] for k in ("title", "section", "product", "updated") if k in meta
        }
        docs.append(Document(doc_id, body.strip(), keep))
    return docs


def kb_sha256(kb_dir: Path | str) -> str:
    root = Path(kb_dir)
    h = hashlib.sha256()
    for md in sorted(root.rglob("*.md")):
        h.update(md.relative_to(root).as_posix().encode())
        h.update(hashlib.sha256(md.read_bytes()).digest())
    return h.hexdigest()


def build_index(
    kb_dir: Path | str,
    index_dir: Path | str | None,
    embedder: Embeddings,
    *,
    model: str,
    size: int = DEFAULT_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> tuple[VectorStore, dict[str, Any]]:
    docs = load_kb(kb_dir)
    chunks = [
        c
        for d in docs
        for c in chunk_document(d.doc_id, d.text, d.metadata, size, overlap)
    ]
    vectors: list[list[float]] = []
    for i in range(0, len(chunks), EMBED_BATCH):
        vectors += embedder.embed([c.text for c in chunks[i : i + EMBED_BATCH]])
    store = VectorStore(index_dir)
    store.add(chunks, vectors)
    info = {
        "model": model,
        "chunk_size": size,
        "overlap": overlap,
        "chunk_count": len(chunks),
        "dim": len(vectors[0]) if vectors else 0,
        "chunk_sha256": chunks_sha256(chunks),
        "kb_sha256": kb_sha256(kb_dir),
    }
    store.write_manifest(info)
    return store, info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("kb", help="knowledge base folder, e.g. data/kb")
    ap.add_argument("--index", help="index folder (default under data/index/)")
    ap.add_argument(
        "--chunk", default=f"{DEFAULT_SIZE}/{DEFAULT_OVERLAP}", help="size/overlap"
    )
    ap.add_argument("--fake", action="store_true", help="offline: the fake embedder")
    args = ap.parse_args(argv)
    size, overlap = (int(x) for x in args.chunk.split("/"))
    embedder: Any
    if args.fake:
        from milo.rag.embed import FakeEmbedder

        embedder = FakeEmbedder()
    else:
        from milo.rag.embed import live_embedder

        try:
            embedder = live_embedder()
        except PermissionError as exc:
            print(f"refusing to run: {exc}", file=sys.stderr)
            return 2
    model = str(embedder.model)
    slug = model.replace("/", "_")
    out = Path(args.index) if args.index else INDEX_ROOT / f"{slug}-{size}-{overlap}"
    _, info = build_index(
        args.kb, out, embedder, model=model, size=size, overlap=overlap
    )
    print(f"indexed {info['chunk_count']} chunks (dim {info['dim']}) into {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
