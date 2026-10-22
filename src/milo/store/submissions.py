"""Submissions in Postgres (SPEC D41: a revision's snapshot never changes; the report is filled once)."""

from __future__ import annotations

import json
from typing import Any

from milo.errors import NotFound, PolicyDenied
from milo.store.common import SUBMISSION_STATUSES, check_choice, uid, user_hex
from milo.store.db import Database, fetch_all, fetch_one, require_row

SUBMISSION_COLS = (
    "id, session_id, revision, submitted_at, status, spec_snapshot_json, report_json, report_html, "
    "estimate_status, cost_low, cost_base, cost_high, currency, rate_card_version, model_id"
)


class PgSubmissions:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self, owner_id: str, session_id: str, spec_snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        """Next revision for the session; marks the session `submitted`."""
        who, sid = user_hex(owner_id), uid(session_id)
        with self.db.transaction(who) as conn:
            require_row(
                conn, "select 1 from sessions where id = %s::uuid for update", (sid,)
            )
            row = fetch_one(
                conn,
                "insert into submissions (session_id, revision, spec_snapshot_json) values "
                "(%s::uuid, coalesce((select max(revision) from submissions where session_id = %s::uuid), 0) + 1, %s::jsonb) "
                f"returning {SUBMISSION_COLS}",
                (sid, sid, json.dumps(spec_snapshot, default=str)),
            )
            conn.execute(
                "update sessions set status = 'submitted', updated_at = now() where id = %s::uuid and status = 'active'",
                (sid,),
            )
        if row is None:
            raise NotFound()
        return row

    def get(self, owner_id: str, submission_id: int) -> dict[str, Any]:
        who = user_hex(owner_id)
        row = self.db.one(
            who,
            f"select {SUBMISSION_COLS} from submissions where id = %s",
            (int(submission_id),),
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
                f"select {SUBMISSION_COLS} from submissions where session_id = %s::uuid order by revision",
                (sid,),
            )

    def latest(self, owner_id: str, session_id: str) -> dict[str, Any] | None:
        rows = self.for_session(owner_id, session_id)
        return rows[-1] if rows else None

    def attach_report(
        self,
        owner_id: str,
        submission_id: int,
        *,
        report_json: dict[str, Any],
        report_html: str | None = None,
        estimate_status: str | None = None,
        cost_low: float | None = None,
        cost_base: float | None = None,
        cost_high: float | None = None,
        currency: str | None = None,
        rate_card_version: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Fill the report once and set the status to `report_ready`. A second call raises `PolicyDenied`."""
        who = user_hex(owner_id)
        row = self.db.one(
            who,
            "update submissions set report_json = %s::jsonb, report_html = %s, estimate_status = %s, "
            "cost_low = %s, cost_base = %s, cost_high = %s, currency = %s, rate_card_version = %s, "
            "model_id = %s, status = 'report_ready' where id = %s and report_json is null "
            f"returning {SUBMISSION_COLS}",
            (
                json.dumps(report_json, default=str),
                report_html,
                estimate_status,
                cost_low,
                cost_base,
                cost_high,
                currency,
                rate_card_version,
                model_id,
                int(submission_id),
            ),
        )
        if row is not None:
            return row
        self.get(who, submission_id)
        raise PolicyDenied("report_already_set")

    def set_status(
        self, owner_id: str, submission_id: int, status: str
    ) -> dict[str, Any]:
        who = user_hex(owner_id)
        check_choice(status, SUBMISSION_STATUSES, "submission_status")
        row = self.db.one(
            who,
            f"update submissions set status = %s where id = %s returning {SUBMISSION_COLS}",
            (status, int(submission_id)),
        )
        if row is None:
            raise NotFound()
        return row
