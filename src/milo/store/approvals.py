"""Approvals in Postgres (SPEC section 8, D48). Only the owning session can resolve one; any other owner gets not_found."""

from __future__ import annotations

from typing import Any

from milo.errors import NotFound, PolicyDenied
from milo.store.common import (
    APPROVAL_STATUSES,
    APPROVAL_TRANSITIONS,
    check_choice,
    uid,
    user_hex,
)
from milo.store.db import Database, fetch_one, require_row
from milo.store.memory import args_hash

APPROVAL_TTL_S = 600.0
APPROVAL_COLS = (
    "id, session_id, turn_id, tool_name, args_hash, status, created_at, expires_at"
)


class PgApprovals:
    def __init__(self, db: Database, ttl_s: float = APPROVAL_TTL_S) -> None:
        self.db = db
        self.ttl_s = ttl_s

    def create(
        self,
        owner_id: str,
        session_id: str,
        tool_name: str,
        args: dict[str, Any],
        *,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        who, sid = user_hex(owner_id), uid(session_id)
        tid = uid(turn_id) if turn_id is not None else None
        with self.db.transaction(who) as conn:
            require_row(conn, "select 1 from sessions where id = %s::uuid", (sid,))
            if tid is not None:
                require_row(
                    conn,
                    "select 1 from turns where id = %s::uuid and session_id = %s::uuid",
                    (tid, sid),
                )
            row = fetch_one(
                conn,
                "insert into approvals (session_id, turn_id, tool_name, args_hash, expires_at) values "
                "(%s::uuid, %s::uuid, %s, %s, now() + make_interval(secs => %s)) "
                f"returning {APPROVAL_COLS}",
                (sid, tid, tool_name, args_hash(args), self.ttl_s),
            )
        if row is None:
            raise NotFound()
        return row

    def get(self, owner_id: str, approval_id: int) -> dict[str, Any]:
        """The approval, with a pending one past its expiry written as `expired` (D48 step 4)."""
        who = user_hex(owner_id)
        with self.db.transaction(who) as conn:
            conn.execute(
                "update approvals set status = 'expired' where id = %s and status = 'pending' and expires_at <= now()",
                (int(approval_id),),
            )
            row = fetch_one(
                conn,
                f"select {APPROVAL_COLS} from approvals where id = %s",
                (int(approval_id),),
            )
        if row is None:
            raise NotFound()
        return row

    def transition(self, owner_id: str, approval_id: int, to: str) -> dict[str, Any]:
        """Move to a new status along `APPROVAL_TRANSITIONS`, atomically; anything else is `PolicyDenied`."""
        who = user_hex(owner_id)
        check_choice(to, APPROVAL_STATUSES, "approval_status")
        with self.db.transaction(who) as conn:
            current = fetch_one(
                conn,
                "select status, (expires_at <= now()) as past from approvals where id = %s for update",
                (int(approval_id),),
            )
            if current is None:
                raise NotFound()
            lapsed = current["status"] == "pending" and bool(current["past"])
            status = "expired" if lapsed else str(current["status"])
            if to not in APPROVAL_TRANSITIONS[status] and not (
                lapsed and to == "expired"
            ):
                raise PolicyDenied("approval_state")
            row = fetch_one(
                conn,
                f"update approvals set status = %s where id = %s returning {APPROVAL_COLS}",
                (to, int(approval_id)),
            )
        if row is None:
            raise NotFound()
        return row
