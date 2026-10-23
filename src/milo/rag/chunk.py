"""Chunker (SPEC section 4, D9). Heading-aware, paragraph-packed, with a character overlap.

Rules, in order:
1. A heading line (`#` to `######`) starts a new section; a chunk never crosses a section boundary.
2. Inside a section, paragraphs (blank-line separated) are packed into a chunk up to `size` characters.
   A paragraph that fits is never cut, so a quote inside one paragraph lands whole in at least one chunk.
3. A new chunk in the same section starts with the last `overlap` characters of the previous chunk (cut at a
   word boundary), when that still fits the size.
4. A paragraph longer than `size` is cut in windows of `size` characters that step by `size - overlap`.

5. When the metadata has a `title`, each chunk's text starts with it (D168), except a chunk that opens with a
   top-level heading. The prefix is added after splitting, so it does not count against `size`.

Chunk id is `<doc_id>#<index>`, the index counting from 0 within the document. Metadata keys that are absent
stay absent; `updated_num` (int YYYYMMDD) exists only when `updated` does (SPEC section 4).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

SETTINGS: tuple[tuple[int, int], ...] = ((800, 100), (400, 50), (1500, 150))
DEFAULT_SIZE, DEFAULT_OVERLAP = SETTINGS[0]
TITLE_PREFIX = False  # D168: measured in kb-edit-v1, no gain on dev, so off

_HEADING = re.compile(r"^#{1,6}\s")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.S)


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    index: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_front_matter(raw: str) -> tuple[dict[str, str], str]:
    """Split `key: value` front matter from the body. No front matter gives `({}, raw)`."""
    m = _FRONT.match(raw)
    if not m:
        return {}, raw
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            meta[key.strip()] = value.strip()
    return meta, raw[m.end() :]


def normalise_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Drop absent (None or empty) keys; add `updated_num` when `updated` is a YYYY-MM-DD date."""
    out = {k: v for k, v in meta.items() if v is not None and v != ""}
    updated = out.get("updated")
    if updated is not None:
        m = _DATE.match(str(updated))
        if m:
            out["updated_num"] = int("".join(m.groups()))
    return out


def _sections(body: str) -> list[list[str]]:
    sections: list[list[str]] = []
    cur: list[str] = []
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para:
            continue
        if _HEADING.match(para) and cur:
            sections.append(cur)
            cur = []
        cur.append(para)
    if cur:
        sections.append(cur)
    return sections


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return text if overlap > 0 else ""
    tail = text[-overlap:]
    cut = tail.find(" ")
    return tail[cut + 1 :] if 0 <= cut < len(tail) - 1 else tail


def _windows(para: str, size: int, overlap: int) -> list[str]:
    out: list[str] = []
    start = 0
    step = max(1, size - overlap)
    while start < len(para):
        out.append(para[start : start + size])
        if start + size >= len(para):
            break
        start += step
    return out


def split_text(
    body: str, size: int = DEFAULT_SIZE, overlap: int = DEFAULT_OVERLAP
) -> list[str]:
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("need size > 0 and 0 <= overlap < size")
    chunks: list[str] = []
    for section in _sections(body):
        cur = ""
        for para in section:
            if len(para) > size:
                if cur:
                    chunks.append(cur)
                    cur = ""
                pieces = _windows(para, size, overlap)
                chunks.extend(pieces[:-1])
                cur = pieces[-1]
            elif not cur:
                cur = para
            elif len(cur) + 2 + len(para) <= size:
                cur = f"{cur}\n\n{para}"
            else:
                chunks.append(cur)
                tail = _tail(cur, overlap)
                cur = (
                    f"{tail}\n\n{para}"
                    if tail and len(tail) + 2 + len(para) <= size
                    else para
                )
        if cur:
            chunks.append(cur)
    return chunks


def chunk_document(
    doc_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
    size: int = DEFAULT_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    title_prefix: bool = TITLE_PREFIX,
) -> list[Chunk]:
    meta = normalise_metadata(dict(metadata or {}))
    title = str(meta.get("title", "")).strip() if title_prefix else ""
    return [
        Chunk(f"{doc_id}#{i}", doc_id, i, with_title(piece, title), dict(meta))
        for i, piece in enumerate(split_text(text, size, overlap))
    ]


def with_title(piece: str, title: str) -> str:
    """Prefix the article title (D168): many sections never name their subject. The size budget and the
    overlap are computed on the body alone, and a chunk that already opens with a top-level heading is left."""
    if not title or piece.startswith("# "):
        return piece
    return f"{title}\n\n{piece}"


def chunks_sha256(chunks: list[Chunk]) -> str:
    payload = [[c.chunk_id, c.text] for c in chunks]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
