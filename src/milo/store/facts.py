"""Facts, fact history and evidence in Postgres (SPEC section 15, D134, D141)."""

from __future__ import annotations

import json
from typing import Any

from milo.errors import NotFound
from milo.store.common import (
    EVIDENCE_KINDS,
    FACT_STATUSES,
    check_choice,
    uid,
    user_hex,
)
from milo.store.db import Database, fetch_all, fetch_one, require_row

FACT_COLS = "session_id, field_id, value_json, unit, period, status, source_message_id, updated_at"
HISTORY_COLS = (
    "id, session_id, field_id, value_json, status, source_message_id, changed_at"
)
EVIDENCE_COLS = "id, session_id, message_id, kind, ref, score, text_hash, created_at"


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str)


class PgFacts:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(
        self,
        owner_id: str,
        session_id: str,
        field_id: str,
        *,
        value: Any,
        status: str,
        unit: str | None = None,
        period: str | None = None,
        source_message_id: int | None = None,
    ) -> dict[str, Any]:
        """Write the current value and append the same state to `fact_history` in one transaction."""
        who, sid = user_hex(owner_id), uid(session_id)
        check_choice(status, FACT_STATUSES, "fact_status")
        with self.db.transaction(who) as conn:
            require_row(conn, "select 1 from sessions where id = %s::uuid", (sid,))
            if source_message_id is not None:
                require_row(
                    conn,
                    "select 1 from messages where id = %s and session_id = %s::uuid",
                    (int(source_message_id), sid),
                )
            row = fetch_one(
                conn,
                "insert into facts (session_id, field_id, value_json, unit, period, status, source_message_id) "
                "values (%s::uuid, %s, %s::jsonb, %s, %s, %s, %s) "
                "on conflict (session_id, field_id) do update set value_json = excluded.value_json, "
                "unit = excluded.unit, period = excluded.period, status = excluded.status, "
                "source_message_id = excluded.source_message_id, updated_at = now() "
                f"returning {FACT_COLS}",
                (sid, field_id, _json(value), unit, period, status, source_message_id),
            )
            conn.execute(
                "insert into fact_history (session_id, field_id, value_json, status, source_message_id) "
                "values (%s::uuid, %s, %s::jsonb, %s, %s)",
                (sid, field_id, _json(value), status, source_message_id),
            )
        if row is None:
            raise NotFound()
        return row

    def get(self, owner_id: str, session_id: str, field_id: str) -> dict[str, Any]:
        who, sid = user_hex(owner_id), uid(session_id)
        row = self.db.one(
            who,
            f"select {FACT_COLS} from facts where session_id = %s::uuid and field_id = %s",
            (sid, field_id),
        )
        if row is None:
            raise NotFound()
        return row

    def for_session(self, owner_id: str, session_id: str) -> list[dict[str, Any]]:
        who, sid = user_hex(owner_id), uid(session_id)
        with self.db.transaction(who) as conn:
            require_row(conn, "select 1 from sessions where id = %s::uuid", (sid,))
            return fetch_all(
                conn,
                f"select {FACT_COLS} from facts where session_id = %s::uuid order by field_id",
                (sid,),
            )

    def field_history(
        self, owner_id: str, session_id: str, field_id: str
    ) -> list[dict[str, Any]]:
        who, sid = user_hex(owner_id), uid(session_id)
        with self.db.transaction(who) as conn:
            require_row(conn, "select 1 from sessions where id = %s::uuid", (sid,))
            return fetch_all(
                conn,
                f"select {HISTORY_COLS} from fact_history where session_id = %s::uuid and field_id = %s order by id",
                (sid, field_id),
            )


class PgEvidence:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self,
        owner_id: str,
        session_id: str,
        kind: str,
        *,
        ref: str | None = None,
        score: float | None = None,
        text_hash: str | None = None,
        message_id: int | None = None,
    ) -> dict[str, Any]:
        who, sid = user_hex(owner_id), uid(session_id)
        check_choice(kind, EVIDENCE_KINDS, "evidence_kind")
        with self.db.transaction(who) as conn:
            require_row(conn, "select 1 from sessions where id = %s::uuid", (sid,))
            if message_id is not None:
                require_row(
                    conn,
                    "select 1 from messages where id = %s and session_id = %s::uuid",
                    (int(message_id), sid),
                )
            row = fetch_one(
                conn,
                "insert into evidence (session_id, message_id, kind, ref, score, text_hash) "
                f"values (%s::uuid, %s, %s, %s, %s, %s) returning {EVIDENCE_COLS}",
                (sid, message_id, kind, ref, score, text_hash),
            )
        if row is None:
            raise NotFound()
        return row

    def for_session(self, owner_id: str, session_id: str) -> list[dict[str, Any]]:
        who, sid = user_hex(owner_id), uid(session_id)
        with self.db.transaction(who) as conn:
            require_row(conn, "select 1 from sessions where id = %s::uuid", (sid,))
            return fetch_all(
                conn,
                f"select {EVIDENCE_COLS} from evidence where session_id = %s::uuid order by id",
                (sid,),
            )
