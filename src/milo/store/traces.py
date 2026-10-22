"""Traces in Postgres (SPEC D15, D141). `detail_json` never holds owner text; `check_trace_detail` enforces it."""

from __future__ import annotations

import json
from typing import Any

from milo.errors import NotFound
from milo.store.common import check_trace_detail, uid, user_hex
from milo.store.db import Database, fetch_all, fetch_one, require_row

TRACE_COLS = "id, turn_id, ts, node, duration_ms, status, error_code, detail_json"


class PgTraces:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        owner_id: str,
        turn_id: str,
        node: str,
        *,
        duration_ms: float = 0.0,
        status: str = "ok",
        error_code: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        who, tid = user_hex(owner_id), uid(turn_id)
        clean = check_trace_detail(dict(detail or {}))
        with self.db.transaction(who) as conn:
            require_row(conn, "select 1 from turns where id = %s::uuid", (tid,))
            row = fetch_one(
                conn,
                "insert into traces (turn_id, node, duration_ms, status, error_code, detail_json) "
                f"values (%s::uuid, %s, %s, %s, %s, %s::jsonb) returning {TRACE_COLS}",
                (
                    tid,
                    node,
                    duration_ms,
                    status,
                    error_code,
                    json.dumps(clean, default=str),
                ),
            )
        if row is None:
            raise NotFound()
        return row

    def for_turn(self, owner_id: str, turn_id: str) -> list[dict[str, Any]]:
        who, tid = user_hex(owner_id), uid(turn_id)
        with self.db.transaction(who) as conn:
            require_row(conn, "select 1 from turns where id = %s::uuid", (tid,))
            return fetch_all(
                conn,
                f"select {TRACE_COLS} from traces where turn_id = %s::uuid order by id",
                (tid,),
            )
