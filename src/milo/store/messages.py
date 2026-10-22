"""Messages in Postgres (SPEC section 15). Content is stored as written; traces never copy it (D141)."""

from __future__ import annotations

from typing import Any

from milo.errors import InvalidInput, NotFound
from milo.store.common import MESSAGE_ROLES, check_choice, uid, user_hex
from milo.store.db import Database, fetch_all, fetch_one, require_row

MESSAGE_COLS = "id, session_id, role, content, created_at, trace_hash"


def check_content(content: str) -> str:
    if not content or not content.strip():
        raise InvalidInput("empty")
    return content


class PgMessages:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self,
        owner_id: str,
        session_id: str,
        role: str,
        content: str,
        trace_hash: str | None = None,
    ) -> dict[str, Any]:
        who, sid = user_hex(owner_id), uid(session_id)
        check_choice(role, MESSAGE_ROLES, "message_role")
        check_content(content)
        with self.db.transaction(who) as conn:
            require_row(conn, "select 1 from sessions where id = %s::uuid", (sid,))
            row = fetch_one(
                conn,
                "insert into messages (session_id, role, content, trace_hash) values (%s::uuid, %s, %s, %s) "
                f"returning {MESSAGE_COLS}",
                (sid, role, content, trace_hash),
            )
            conn.execute(
                "update sessions set updated_at = now() where id = %s::uuid", (sid,)
            )
        if row is None:
            raise NotFound()
        return row

    def get(self, owner_id: str, session_id: str, message_id: int) -> dict[str, Any]:
        who, sid = user_hex(owner_id), uid(session_id)
        row = self.db.one(
            who,
            f"select {MESSAGE_COLS} from messages where id = %s and session_id = %s::uuid",
            (int(message_id), sid),
        )
        if row is None:
            raise NotFound()
        return row

    def history(
        self,
        owner_id: str,
        session_id: str,
        *,
        after_id: int = 0,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        who, sid = user_hex(owner_id), uid(session_id)
        with self.db.transaction(who) as conn:
            require_row(conn, "select 1 from sessions where id = %s::uuid", (sid,))
            sql = f"select {MESSAGE_COLS} from messages where session_id = %s::uuid and id > %s order by id"
            params: list[Any] = [sid, int(after_id)]
            if limit is not None:
                sql += " limit %s"
                params.append(max(1, int(limit)))
            return fetch_all(conn, sql, params)
