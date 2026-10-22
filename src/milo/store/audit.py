"""The audit log and operator actions in Postgres (SPEC D130, D79).

Owners never read `audit_log` (no policy for the app role). These classes expect a `Database` connected as
`milo_operator`: SELECT everywhere, DELETE on customers, INSERT on audit_log, nothing else.
"""

from __future__ import annotations

import json
from typing import Any

from milo.errors import NotFound
from milo.store.common import check_trace_detail, user_hex, uuid_text
from milo.store.db import Database, fetch_all, fetch_one

AUDIT_COLS = "id, actor_user_id, action, target, detail_json, at"


class PgAudit:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        actor_user_id: str | None,
        action: str,
        target: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor = uuid_text(user_hex(actor_user_id)) if actor_user_id else None
        clean = check_trace_detail(dict(detail or {}))
        row = self.db.one(
            None,
            f"insert into audit_log (actor_user_id, action, target, detail_json) values (%s, %s, %s, %s::jsonb) returning {AUDIT_COLS}",
            (actor, action, target, json.dumps(clean, default=str)),
        )
        if row is None:
            raise NotFound()
        return row

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.transaction(None) as conn:
            return fetch_all(
                conn,
                f"select {AUDIT_COLS} from audit_log order by id desc limit %s",
                (max(1, min(limit, 1000)),),
            )


OPS_SUBMISSION_SQL = (
    "select s.id, s.session_id, s.revision, s.submitted_at, s.status, s.estimate_status, s.report_json, "
    "se.title, se.user_id as owner_id, p.email as owner_email "
    "from submissions s join sessions se on se.id = s.session_id left join profiles p on p.id = se.user_id"
)


class PgOperator:
    def __init__(self, db: Database) -> None:
        self.db = db

    def submissions(self, limit: int = 200) -> list[dict[str, Any]]:
        """Every owner's submissions, newest first (operator read, D130 SELECT; M6 operator pages)."""
        with self.db.transaction(None) as conn:
            return fetch_all(
                conn,
                f"{OPS_SUBMISSION_SQL} order by s.id desc limit %s",
                (max(1, min(limit, 1000)),),
            )

    def submission(self, submission_id: int) -> dict[str, Any]:
        row = self.db.one(
            None, f"{OPS_SUBMISSION_SQL} where s.id = %s", (int(submission_id),)
        )
        if row is None:
            raise NotFound()
        return row

    def delete_customer(self, actor_user_id: str, customer_id: str) -> None:
        """Delete a customer (cascades to the owner's rows) and write the audit row in the same transaction."""
        actor = uuid_text(user_hex(actor_user_id))
        target = uuid_text(user_hex(customer_id))
        with self.db.transaction(None) as conn:
            gone = fetch_one(
                conn,
                "delete from customers where user_id = %s returning user_id",
                (target,),
            )
            if gone is None:
                raise NotFound()
            conn.execute(
                "insert into audit_log (actor_user_id, action, target, detail_json) values (%s, 'customer_deleted', %s, '{}'::jsonb)",
                (actor, target),
            )
