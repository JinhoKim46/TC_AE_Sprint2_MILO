"""Owner requests in Postgres: field requests and deletion requests (SPEC D79)."""

from __future__ import annotations

from typing import Any

from milo.errors import NotFound
from milo.store.common import uid, user_hex, uuid_text
from milo.store.db import Database, fetch_all, fetch_one, require_row
from milo.store.users import ensure_customer

FIELD_COLS = "id, user_id, session_id, field_id, note, status, created_at"
DELETION_COLS = "id, user_id, reason, status, created_at, resolved_at"


class PgFieldRequests:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self,
        owner_id: str,
        field_id: str,
        *,
        note: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        who = user_hex(owner_id)
        sid = uid(session_id) if session_id is not None else None
        with self.db.transaction(who) as conn:
            ensure_customer(conn, who)
            if sid is not None:
                require_row(conn, "select 1 from sessions where id = %s::uuid", (sid,))
            row = fetch_one(
                conn,
                "insert into field_requests (user_id, session_id, field_id, note) values (%s, %s::uuid, %s, %s) "
                f"returning {FIELD_COLS}",
                (uuid_text(who), sid, field_id, note),
            )
        if row is None:
            raise NotFound()
        return row

    def history(self, owner_id: str) -> list[dict[str, Any]]:
        who = user_hex(owner_id)
        with self.db.transaction(who) as conn:
            return fetch_all(
                conn,
                f"select {FIELD_COLS} from field_requests where user_id = %s order by id",
                (uuid_text(who),),
            )


class PgDeletionRequests:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, owner_id: str, reason: str | None = None) -> dict[str, Any]:
        who = user_hex(owner_id)
        with self.db.transaction(who) as conn:
            ensure_customer(conn, who)
            row = fetch_one(
                conn,
                f"insert into deletion_requests (user_id, reason) values (%s, %s) returning {DELETION_COLS}",
                (uuid_text(who), reason),
            )
        if row is None:
            raise NotFound()
        return row

    def history(self, owner_id: str) -> list[dict[str, Any]]:
        who = user_hex(owner_id)
        with self.db.transaction(who) as conn:
            return fetch_all(
                conn,
                f"select {DELETION_COLS} from deletion_requests where user_id = %s order by id",
                (uuid_text(who),),
            )
