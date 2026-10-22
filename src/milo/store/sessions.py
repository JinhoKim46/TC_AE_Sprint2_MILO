"""Sessions and turns in Postgres (SPEC D97 to D99, D102, D131, D47)."""

from __future__ import annotations

import json
from typing import Any

from milo.errors import NotFound, PolicyDenied
from milo.store.common import (
    SESSION_STATUSES,
    STALE_TURN_S,
    TURN_FINISH_STATUSES,
    TurnRunning,
    check_choice,
    clean_title,
    uid,
    user_hex,
    uuid_text,
)
from milo.store.db import Database, fetch_all, fetch_one, require_row
from milo.store.users import ensure_customer

SESSION_COLS = "id, user_id, title, status, created_at, updated_at, archived_at"
TURN_COLS = "id, session_id, status, created_at, finished_at"
EVENT_COLS = "id, turn_id, seq, type, payload_json, created_at"


def _like(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class PgSessions:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, owner_id: str, title: str | None = None) -> dict[str, Any]:
        who = user_hex(owner_id)
        name = clean_title(title)
        with self.db.transaction(who) as conn:
            ensure_customer(conn, who)
            row = fetch_one(
                conn,
                f"insert into sessions (user_id, title) values (%s, %s) returning {SESSION_COLS}",
                (uuid_text(who), name),
            )
        if row is None:
            raise NotFound()
        return row

    def get(self, owner_id: str, session_id: str) -> dict[str, Any]:
        who, sid = user_hex(owner_id), uid(session_id)
        row = self.db.one(
            who,
            f"select {SESSION_COLS} from sessions where id = %s::uuid",
            (sid,),
        )
        if row is None:
            raise NotFound()
        return row

    def search(
        self,
        owner_id: str,
        *,
        status: str | None = None,
        query: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Newest first. Archived sessions are listed only when `status='archived'` is asked for (SPEC D98)."""
        who = user_hex(owner_id)
        sql = f"select {SESSION_COLS} from sessions where user_id = %s"
        params: list[Any] = [uuid_text(who)]
        if status is None:
            sql += " and status <> 'archived'"
        else:
            check_choice(status, SESSION_STATUSES, "session_status")
            sql += " and status = %s"
            params.append(status)
        if query:
            sql += " and title ilike %s escape '\\'"
            params.append(_like(query))
        sql += " order by updated_at desc, id limit %s"
        params.append(max(1, min(limit, 500)))
        with self.db.transaction(who) as conn:
            return fetch_all(conn, sql, params)

    def rename(self, owner_id: str, session_id: str, title: str) -> dict[str, Any]:
        who, sid = user_hex(owner_id), uid(session_id)
        name = clean_title(title)
        if name is None:
            raise NotFound()
        row = self.db.one(
            who,
            f"update sessions set title = %s, updated_at = now() where id = %s::uuid returning {SESSION_COLS}",
            (name, sid),
        )
        if row is None:
            raise NotFound()
        return row

    def archive(self, owner_id: str, session_id: str) -> dict[str, Any]:
        who, sid = user_hex(owner_id), uid(session_id)
        row = self.db.one(
            who,
            "update sessions set status = 'archived', archived_at = now(), updated_at = now() "
            f"where id = %s::uuid and status <> 'archived' returning {SESSION_COLS}",
            (sid,),
        )
        return row if row is not None else self.get(who, sid)

    def restore(self, owner_id: str, session_id: str) -> dict[str, Any]:
        who, sid = user_hex(owner_id), uid(session_id)
        row = self.db.one(
            who,
            "update sessions s set archived_at = null, updated_at = now(), "
            "status = case when exists (select 1 from submissions b where b.session_id = s.id) "
            "then 'submitted' else 'active' end "
            f"where s.id = %s::uuid and s.status = 'archived' returning {SESSION_COLS}",
            (sid,),
        )
        return row if row is not None else self.get(who, sid)

    def set_status(self, owner_id: str, session_id: str, status: str) -> dict[str, Any]:
        who, sid = user_hex(owner_id), uid(session_id)
        check_choice(status, SESSION_STATUSES, "session_status")
        row = self.db.one(
            who,
            "update sessions set status = %s, updated_at = now(), "
            "archived_at = case when %s::text = 'archived' then coalesce(archived_at, now()) else null end "
            f"where id = %s::uuid returning {SESSION_COLS}",
            (status, status, sid),
        )
        if row is None:
            raise NotFound()
        return row

    def touch(self, owner_id: str, session_id: str) -> None:
        who, sid = user_hex(owner_id), uid(session_id)
        if not self.db.run(
            who, "update sessions set updated_at = now() where id = %s::uuid", (sid,)
        ):
            raise NotFound()


class PgTurns:
    def __init__(self, db: Database, stale_s: float = STALE_TURN_S) -> None:
        self.db = db
        self.stale_s = stale_s

    def start(self, owner_id: str, session_id: str) -> dict[str, Any]:
        """Open a turn. One running turn per account (SPEC D131); a second start raises `TurnRunning`."""
        who, sid = user_hex(owner_id), uid(session_id)
        with self.db.transaction(who) as conn:
            conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (who,))
            conn.execute(
                "update turns set status = 'error', finished_at = now() "
                "where status = 'running' and created_at < now() - make_interval(secs => %s)",
                (self.stale_s,),
            )
            session = fetch_one(
                conn,
                "select status from sessions where id = %s::uuid",
                (sid,),
            )
            if session is None:
                raise NotFound()
            if session["status"] == "archived":
                raise PolicyDenied("session_archived")
            if conn.execute(
                "select 1 from turns where status = 'running' limit 1"
            ).fetchone():
                raise TurnRunning()
            row = fetch_one(
                conn,
                f"insert into turns (session_id) values (%s::uuid) returning {TURN_COLS}",
                (sid,),
            )
        if row is None:
            raise NotFound()
        return row

    def get(self, owner_id: str, turn_id: str) -> dict[str, Any]:
        who, tid = user_hex(owner_id), uid(turn_id)
        row = self.db.one(
            who, f"select {TURN_COLS} from turns where id = %s::uuid", (tid,)
        )
        if row is None:
            raise NotFound()
        return row

    def finish(
        self, owner_id: str, turn_id: str, status: str = "done"
    ) -> dict[str, Any]:
        who, tid = user_hex(owner_id), uid(turn_id)
        check_choice(status, TURN_FINISH_STATUSES, "turn_status")
        row = self.db.one(
            who,
            f"update turns set status = %s, finished_at = now() where id = %s::uuid and status = 'running' returning {TURN_COLS}",
            (status, tid),
        )
        return row if row is not None else self.get(who, tid)

    def add_event(
        self, owner_id: str, turn_id: str, type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Append an event. `payload_json` is the one place answer text is stored (SPEC D141)."""
        who, tid = user_hex(owner_id), uid(turn_id)
        with self.db.transaction(who) as conn:
            require_row(conn, "select 1 from turns where id = %s::uuid", (tid,))
            row = fetch_one(
                conn,
                "insert into turn_events (turn_id, seq, type, payload_json) values "
                "(%s::uuid, coalesce((select max(seq) from turn_events where turn_id = %s::uuid), 0) + 1, %s, %s::jsonb) "
                f"returning {EVENT_COLS}",
                (tid, tid, type, json.dumps(payload, default=str)),
            )
        if row is None:
            raise NotFound()
        return row

    def events(self, owner_id: str, turn_id: str) -> list[dict[str, Any]]:
        who, tid = user_hex(owner_id), uid(turn_id)
        with self.db.transaction(who) as conn:
            require_row(conn, "select 1 from turns where id = %s::uuid", (tid,))
            return fetch_all(
                conn,
                f"select {EVENT_COLS} from turn_events where turn_id = %s::uuid order by seq",
                (tid,),
            )
