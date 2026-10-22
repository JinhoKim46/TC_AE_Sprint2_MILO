"""Development factory: an in-memory store and a fake App (no model, no database, no network).

`uv run flask --app milo.web.dev:create_dev_app run`. Refuses to start in production. The seeded
accounts are fictional and only exist in this process (SPEC D70, HARNESS rule 5).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

from flask import Flask
from werkzeug.security import check_password_hash, generate_password_hash

from milo.settings import Settings, is_prod
from milo.types import Response
from milo.web.routes import create_flask_app

OWNER_EMAIL = "owner@example.test"
OPERATOR_EMAIL = "operator@example.test"
DEV_PASSWORD = "dev-only-password"  # dev factory only; the accounts live in memory


class FakeApp:
    """The slice of `App` the web layer uses (SPEC section 16)."""

    def __init__(self) -> None:
        self.traces: list[dict[str, Any]] = [
            {"event": "validate", "thread_id": "t1", "status": "ok", "duration_ms": 1.0}
        ]

    def ask(
        self, question: str, user_id: str, thread_id: str, on_event: Any = None
    ) -> Response:
        return Response(answer="(dev) " + question)

    def resume_approval(
        self, approval_id: str, decision: str, user_id: str
    ) -> Response:
        return Response(answer="(dev) " + decision)

    def recent_traces(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.traces[:limit]


class DevStore:
    """In-memory implementation of `milo.web.ports.WebStore`."""

    def __init__(self, *, seed: bool = True) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.pending: dict[str, str] = {}
        self.resets: dict[str, str] = {}
        self.consents: dict[str, list[dict[str, Any]]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.specs: dict[str, dict[str, Any]] = {}
        self.reports: dict[str, dict[str, Any]] = {}
        self.submissions: dict[str, dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []
        if seed:
            self._seed()

    # ---- accounts ----

    def add_user(
        self, email: str, password: str, first: str, role: str = "owner"
    ) -> str:
        uid = secrets.token_hex(8)
        self.users[uid] = {
            "id": uid,
            "email": email,
            "first_name": first,
            "role": role,
            "hash": generate_password_hash(password),
            "confirmed": True,
        }
        return uid

    def authenticate(self, email: str, password: str) -> str | None:
        for u in self.users.values():
            if (
                u["email"] == email
                and u["confirmed"]
                and check_password_hash(u["hash"], password)
            ):
                return str(u["id"])
        return None

    def sign_up(self, email: str, password: str, first_name: str) -> str:
        if any(u["email"] == email for u in self.users.values()):
            return ""
        uid = self.add_user(email, password, first_name)
        self.users[uid]["confirmed"] = False
        token = secrets.token_urlsafe(16)
        self.pending[token] = uid
        return token

    def confirm(self, token: str) -> bool:
        uid = self.pending.pop(token, None)
        if uid is None:
            return False
        self.users[uid]["confirmed"] = True
        return True

    def request_reset(self, email: str) -> None:
        for u in self.users.values():
            if u["email"] == email:
                self.resets[secrets.token_urlsafe(16)] = u["id"]

    def reset_password(self, token: str, password: str) -> bool:
        uid = self.resets.pop(token, None)
        if uid is None:
            return False
        self.users[uid]["hash"] = generate_password_hash(password)
        return True

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        u = self.users.get(user_id)
        return (
            None
            if u is None
            else {k: v for k, v in u.items() if k not in ("hash", "confirmed")}
        )

    # ---- consent ----

    def has_consent(self, user_id: str, purpose: str) -> bool:
        return any(c["purpose"] == purpose for c in self.consents.get(user_id, []))

    def record_consent(self, user_id: str, purpose: str, version: str) -> None:
        self.consents.setdefault(user_id, []).append(
            {"purpose": purpose, "version": version, "at": datetime.now()}
        )

    def list_consents(self, user_id: str) -> list[dict[str, Any]]:
        return list(self.consents.get(user_id, []))

    # ---- sessions ----

    def add_session(
        self, user_id: str, title: str, *, status: str = "active", age_days: float = 0
    ) -> str:
        sid = secrets.token_hex(6)
        self.sessions[sid] = {
            "id": sid,
            "user_id": user_id,
            "title": title,
            "status": status,
            "updated_at": datetime.now() - timedelta(days=age_days),
        }
        self.messages[sid] = []
        return sid

    def list_sessions(
        self, user_id: str, *, query: str = "", archived: bool = False
    ) -> list[dict[str, Any]]:
        want = "archived" if archived else None
        rows = [
            s
            for s in self.sessions.values()
            if s["user_id"] == user_id
            and ((s["status"] == "archived") if want else (s["status"] != "archived"))
            and query.lower() in s["title"].lower()
        ]
        return sorted(rows, key=lambda s: s["updated_at"], reverse=True)

    def get_session(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        s = self.sessions.get(session_id)
        return s if s and s["user_id"] == user_id else None

    def list_messages(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        return (
            list(self.messages.get(session_id, []))
            if self.get_session(user_id, session_id)
            else []
        )

    def get_spec(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        return (
            self.specs.get(session_id)
            if self.get_session(user_id, session_id)
            else None
        )

    # ---- reports and operator ----

    def list_reports(self, user_id: str) -> list[dict[str, Any]]:
        return [r for r in self.reports.values() if r["user_id"] == user_id]

    def get_report(self, user_id: str, report_id: str) -> dict[str, Any] | None:
        r = self.reports.get(report_id)
        return r if r and r["user_id"] == user_id else None

    def ops_submissions(self) -> list[dict[str, Any]]:
        return list(self.submissions.values())

    def ops_submission(self, submission_id: str) -> dict[str, Any] | None:
        return self.submissions.get(submission_id)

    def ops_audit(self) -> list[dict[str, Any]]:
        return list(self.audit)

    # ---- seed ----

    def _seed(self) -> None:
        owner = self.add_user(OWNER_EMAIL, DEV_PASSWORD, "Ada")
        self.add_user(OPERATOR_EMAIL, DEV_PASSWORD, "Ops", role="operator")
        for uid in self.users:
            self.record_consent(uid, "interview", "v0")
        sid = self.add_session(owner, "Invoice follow-up automation")
        self.add_session(owner, "Weekly stock report", age_days=3)
        self.add_session(owner, "Old idea", age_days=30)
        self.add_session(owner, "Shelved diagnosis", status="archived", age_days=10)
        self.messages[sid] = [
            {
                "role": "user",
                "text": "We chase late invoices by hand.\nIt takes hours.",
            },
            {
                "role": "assistant",
                "text": "How many invoices go out each month?",
                "details": {
                    "sources": [
                        {
                            "chunk_id": "kb-001",
                            "document": "Invoicing basics",
                            "score": 0.71,
                        }
                    ],
                    "tool_calls": [],
                    "cost_usd": 0.0012,
                },
            },
        ]
        self.specs[sid] = {
            "progress": 2,
            "total": 6,
            "fields": [
                {
                    "id": "f1",
                    "label": "Process",
                    "value": "Invoice chasing",
                    "status": "reported",
                    "quote": "We chase late invoices by hand.",
                },
                {
                    "id": "f2",
                    "label": "Volume",
                    "value": "",
                    "status": "unknown",
                    "quote": "",
                },
            ],
            "missing": ["Volume", "Tools in use"],
            "estimate": None,
        }
        rid = "r1"
        self.reports[rid] = {
            "id": rid,
            "user_id": owner,
            "title": "Invoice follow-up report",
            "created_at": datetime.now(),
            "sections": [{"heading": "Summary", "text": "Indicative summary."}],
        }
        self.submissions["s1"] = {
            "id": "s1",
            "customer": "Ada",
            "status": "submitted",
            "title": "Invoice follow-up automation",
            "at": datetime.now(),
        }
        self.audit.append(
            {
                "at": datetime.now(),
                "actor": "operator",
                "action": "view",
                "target": "s1",
            }
        )


def create_dev_app() -> Flask:
    if is_prod():
        raise RuntimeError("the dev factory refuses to run with MILO_ENV=prod")
    settings = Settings(env="test")
    return create_flask_app(FakeApp(), DevStore(), settings, debug=True)
