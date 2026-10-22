"""Trace records (SPEC D15, section 9). Customer text is stored as hash and length, never as text (D141)."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def text_ref(text: str) -> dict[str, Any]:
    """Hash and length of a customer text, the only form that may enter a trace."""
    return {"hash": text_hash(text), "length": len(text)}


@dataclass
class TraceRecord:
    ts: float
    thread_id: str
    turn_id: str
    node: str
    duration_ms: float = 0.0
    status: str = "ok"
    error_code: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class Tracer:
    """In-memory trace list, optionally mirrored to a JSON-lines file (`logs/trace.jsonl`)."""

    def __init__(
        self, path: Path | str | None = None, clock: Callable[[], float] = time.time
    ) -> None:
        self.records: list[TraceRecord] = []
        self.path = Path(path) if path else None
        self._clock = clock

    def record(
        self,
        node: str,
        *,
        thread_id: str = "",
        turn_id: str = "",
        duration_ms: float = 0.0,
        status: str = "ok",
        error_code: str | None = None,
        **detail: Any,
    ) -> TraceRecord:
        rec = TraceRecord(
            self._clock(),
            thread_id,
            turn_id,
            node,
            duration_ms,
            status,
            error_code,
            detail,
        )
        self.records.append(rec)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(rec), sort_keys=True, default=str) + "\n")
        return rec

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return [asdict(r) for r in self.records[-limit:]][::-1]
