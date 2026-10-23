"""Local wiring without a database, a network or a model: the in-memory store, in-memory accounts and a scripted
chat model behind the real discovery `App`, `TurnRunner` and `WebData` (SPEC D70; tests, the dev server and
`evals/ui/record.py`).

`uv run flask --app milo.web.dev:create_dev_app run`. Refuses to start in production. The seeded accounts are
fictional and exist only in this process (HARNESS rule 5).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import Flask

from milo.settings import Settings, is_prod
from milo.types import ChatReply, ToolCall, Usage
from milo.web.accounts import MemoryAccounts
from milo.web.data import WebData
from milo.web.routes import create_flask_app

OWNER_EMAIL = "owner@example.test"
OPERATOR_EMAIL = "operator@example.test"
DEV_PASSWORD = "dev-only-password"  # dev factory only; the accounts live in memory
KB_DIR = Path(__file__).resolve().parents[3] / "data" / "kb"

# The scripted owner walk (fictional): each message names facts that appear in it verbatim (D134).
DEMO_MESSAGES = (
    "My name is Ada Lovelace and I run Bright Cuts, a hair salon business with 12 staff across 2 salons.",
    "Bookings come in by phone and email, about 40 requests a day. Each one takes 6 minutes to handle "
    "and 3 minutes of typing. We use Fresha and Gmail. Our steps: answer, check the calendar, book, confirm.",
    "Calls get missed when we are busy and some bookings are delayed. We want to save time by automating "
    "phone booking. We can export a CSV from Fresha. We store client names and phone numbers. "
    "Urgent requests do not really happen.",
)


def _f(field_id: str, value: Any, evidence: str, **extra: Any) -> dict[str, Any]:
    return {"field_id": field_id, "value": value, "evidence_text": evidence, **extra}


DEMO_FACTS: dict[str, list[dict[str, Any]]] = {
    "Ada Lovelace": [
        _f("owner_name", "Ada Lovelace", "My name is Ada Lovelace"),
        _f("company_name", "Bright Cuts", "I run Bright Cuts"),
        _f("industry", "beauty-personal-care", "a hair salon business"),
        _f("employee_count", 12, "12 staff", unit="people"),
        _f("locations", 2, "2 salons", unit="sites"),
    ],
    "about 40 requests a day": [
        _f("request_channels", ["phone", "email"], "by phone and email"),
        _f(
            "daily_request_volume",
            40,
            "about 40 requests a day",
            unit="requests",
            period="day",
        ),
        _f("average_handling_minutes", 6, "takes 6 minutes to handle", unit="minutes"),
        _f("manual_entry_minutes", 3, "3 minutes of typing", unit="minutes"),
        _f("tools_in_use", ["Fresha", "Gmail"], "We use Fresha and Gmail"),
        _f(
            "steps_in_process",
            ["answer", "check the calendar", "book", "confirm"],
            "answer, check the calendar, book, confirm",
        ),
    ],
    "Calls get missed": [
        _f(
            "pain_points",
            ["missed calls when busy"],
            "Calls get missed when we are busy",
        ),
        _f(
            "error_types",
            ["missed", "delayed"],
            "Calls get missed when we are busy and some bookings are delayed",
        ),
        _f(
            "automation_goals", ["automating phone booking"], "automating phone booking"
        ),
        _f("main_goal", "time", "We want to save time"),
        _f("integrations_available", ["csv-export"], "We can export a CSV from Fresha"),
        _f("personal_data_handled", True, "We store client names and phone numbers"),
        _f(
            "personal_data_types",
            ["client names", "phone numbers"],
            "client names and phone numbers",
        ),
        _f("urgent_requests_exist", False, "Urgent requests do not really happen"),
    ],
}

_OWNER_TAG = re.compile(r"<owner_message>\n(.*?)\n</owner_message>", re.S)
_WITHDRAW = re.compile(r"withdraw\D{0,40}(\d+)", re.I)


class DemoChat:
    """A scripted chat model: extraction from `DEMO_FACTS`, a fixed reply, and a `withdraw_submission` tool call
    when the owner asks to withdraw a numbered submission (the blocked action of the M6 walk, SPEC 16)."""

    price_usd_per_mtok = (0.0, 0.0)
    model = "scripted-demo"

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages: list[dict[str, Any]], tools: Any = None) -> ChatReply:
        self.calls += 1
        if str(messages[0].get("content", "")).startswith("You extract facts"):
            text = str(
                messages[-1].get("content") or ""
            )  # owner message and any document excerpt
            facts = [
                f for key, items in DEMO_FACTS.items() if key in text for f in items
            ]
            body = {"facts": facts, "asks_about": "none"}
            return ChatReply(json.dumps(body), usage=Usage(10, 10))
        last = messages[-1]
        if last.get("role") == "tool":
            return ChatReply(
                "Done. The submission is withdrawn. Is there anything else about your process to add?",
                usage=Usage(10, 10),
            )
        match = _WITHDRAW.search(_owner_text(messages))
        if tools and match:
            call = ToolCall(
                "w1", "withdraw_submission", {"submission_id": int(match.group(1))}
            )
            return ChatReply("", tool_calls=[call], usage=Usage(10, 10))
        return ChatReply(
            "Thank you, I noted that. What else can you tell me about how requests reach your business?",
            usage=Usage(10, 10),
        )


def _owner_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        content = str(m.get("content") or "")
        found = _OWNER_TAG.search(content)
        if found:
            return found.group(1)
        if m.get("role") == "user":
            return content
    return ""


@dataclass
class Local:
    flask: Flask
    data: WebData
    accounts: MemoryAccounts
    app: Any


def build_local(
    chat: Any = None,
    *,
    settings: Settings | None = None,
    debug: bool = True,
    spawn: Callable[[Callable[[], None]], None] | None = None,
    kb: bool = False,
    report_builder: Any = None,
) -> Local:
    """The real discovery wiring over the in-memory store (the same shape as `create_app` builds over Postgres)."""
    if is_prod():
        raise RuntimeError("the local factory refuses to run with MILO_ENV=prod")
    from milo.app import build_discovery_app
    from milo.facts.store import SessionFacts
    from milo.protocol.loader import get_protocol
    from milo.rag.embed import FakeEmbedder
    from milo.store import memory_repositories
    from milo.tools.backend import MiloBackend

    repos = memory_repositories()
    facts = SessionFacts(repos, get_protocol())
    backend = MiloBackend(retriever=None, store=facts)  # type: ignore[arg-type]
    app = build_discovery_app(
        chat or DemoChat(), FakeEmbedder(), backend, session_facts=facts
    )
    if kb:
        from milo.rag.ingest import load_kb

        app.ingest(load_kb(KB_DIR))
    backend.retriever = app.retriever
    accounts = MemoryAccounts(repos)
    data = WebData(app, accounts, spawn=spawn, report_builder=report_builder)
    cfg = settings or Settings(env="test")
    return Local(create_flask_app(data, cfg, debug=debug), data, accounts, app)


def seed(local: Local) -> tuple[str, str]:
    """A consenting owner and an operator (the operator role is set by hand, D94)."""
    owner = local.accounts.add_confirmed(OWNER_EMAIL, DEV_PASSWORD, "Ada")
    operator = local.accounts.add_confirmed(
        OPERATOR_EMAIL, DEV_PASSWORD, "Ops", role="operator"
    )
    for uid in (owner, operator):
        local.data.record_consent(uid, "v0")
    return owner, operator


def create_dev_app() -> Flask:
    if is_prod():
        raise RuntimeError("the dev factory refuses to run with MILO_ENV=prod")
    local = build_local(kb=True)
    seed(local)
    return local.flask
