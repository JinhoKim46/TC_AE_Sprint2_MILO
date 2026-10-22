"""In-memory store (SPEC D70, D74). No database, no network: tests and discovery mode use it.

Every owner-facing call takes the owner id and applies the same owner filter that row-level security applies in
Postgres: a row of another owner is invisible (`get` returns None, `select` skips it, `update` and `delete` raise
`NotFound`). The store role (M5-store) builds the typed repositories on top of this.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

from milo.errors import NotFound

APPROVAL_TTL_S = 600.0


def args_hash(args: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(args, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


class MemoryStore:
    """Generic owner-scoped tables, plus the `rate_limits` counter (SPEC D142)."""

    def __init__(self) -> None:
        self._tables: dict[str, dict[int, dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._limits: dict[tuple[str, str, int], int] = {}

    def insert(self, table: str, owner_id: str, row: dict[str, Any]) -> int:
        row_id = next(self._ids)
        self._tables.setdefault(table, {})[row_id] = {
            **row,
            "id": row_id,
            "owner_id": owner_id,
        }
        return row_id

    def get(self, table: str, owner_id: str, row_id: int) -> dict[str, Any] | None:
        row = self._tables.get(table, {}).get(row_id)
        return dict(row) if row and row["owner_id"] == owner_id else None

    def select(self, table: str, owner_id: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self._tables.get(table, {}).values()
            if r["owner_id"] == owner_id
        ]

    def update(
        self, table: str, owner_id: str, row_id: int, changes: dict[str, Any]
    ) -> None:
        row = self._tables.get(table, {}).get(row_id)
        if not row or row["owner_id"] != owner_id:
            raise NotFound(table)
        row.update({k: v for k, v in changes.items() if k not in ("id", "owner_id")})

    def delete(self, table: str, owner_id: str, row_id: int) -> None:
        row = self._tables.get(table, {}).get(row_id)
        if not row or row["owner_id"] != owner_id:
            raise NotFound(table)
        del self._tables[table][row_id]

    def hit(
        self, scope: str, key: str, *, window_s: int, now: float | None = None
    ) -> int:
        """Count one event in the current window and return the count (the `rate_limits` table in Postgres)."""
        t = time.time() if now is None else now
        slot = (scope, key, int(t // window_s))
        self._limits[slot] = self._limits.get(slot, 0) + 1
        return self._limits[slot]


class MemoryApprovals:
    """Approval lifecycle storage for contract mode (SPEC section 8, D48).

    Statuses: pending, approved, kept, used, expired. A row binds (scope, turn, tool, argument hash) to its owner;
    another owner reading it gets None, which the caller reports as `not_found`.
    """

    def __init__(
        self, ttl_s: float = APPROVAL_TTL_S, clock: Callable[[], float] = time.time
    ) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._ttl = ttl_s
        self._clock = clock

    def create(
        self,
        *,
        scope: str,
        owner_id: str,
        turn_id: str,
        tool_name: str,
        args: dict[str, Any],
        context: Any = None,
    ) -> str:
        approval_id = uuid.uuid4().hex
        now = self._clock()
        self._rows[approval_id] = {
            "id": approval_id,
            "scope": scope,
            "owner_id": owner_id,
            "turn_id": turn_id,
            "tool_name": tool_name,
            "args": dict(args),
            "args_hash": args_hash(args),
            "status": "pending",
            "created_at": now,
            "expires_at": now + self._ttl,
            "context": context,
        }
        return approval_id

    def get(self, approval_id: str, owner_id: str) -> dict[str, Any] | None:
        row = self._rows.get(approval_id)
        if not row or row["owner_id"] != owner_id:
            return None
        if row["status"] == "pending" and self._clock() >= row["expires_at"]:
            row["status"] = "expired"
        return dict(row)

    def set_status(self, approval_id: str, owner_id: str, status: str) -> None:
        row = self._rows.get(approval_id)
        if not row or row["owner_id"] != owner_id:
            raise NotFound("approval")
        row["status"] = status
