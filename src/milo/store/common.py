"""Shared rules for the repositories, in memory and in Postgres (SPEC D70, D74, D141).

Both implementations validate with these helpers, so a bad value fails the same way in tests and in production.
Conventions: every uuid leaves the store as 32 lowercase hex characters (D47) and is accepted in either form;
a row of another owner, or a missing row, is `NotFound` and nothing changes.
"""

from __future__ import annotations

import uuid
from typing import Any

from milo.errors import InvalidInput, MiloError, NotFound, PolicyDenied

SESSION_STATUSES = frozenset({"active", "submitted", "archived"})
TURN_FINISH_STATUSES = frozenset({"done", "error"})
MESSAGE_ROLES = frozenset({"owner", "assistant", "system"})
FACT_STATUSES = frozenset(
    {"reported", "confirmed", "estimated", "unknown", "conflicting"}
)
EVIDENCE_KINDS = frozenset(
    {"customer_statement", "uploaded_document", "kb_chunk", "calculation"}
)
SUBMISSION_STATUSES = frozenset({"received", "report_ready", "failed", "withdrawn"})
CONSENT_PURPOSES = frozenset({"interview", "aggregate"})
APPROVAL_STATUSES = frozenset({"pending", "approved", "kept", "used", "expired"})
REQUEST_STATUSES = frozenset({"open", "done", "declined"})

# From -> allowed next statuses (SPEC section 8, D48). `used` and `kept` and `expired` are final.
APPROVAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"approved", "kept", "used", "expired"}),
    "approved": frozenset({"used", "expired"}),
    "kept": frozenset(),
    "used": frozenset(),
    "expired": frozenset(),
}

CUSTOMER_FIELDS = (
    "company_name",
    "industry",
    "employee_count",
    "locations",
    "owner_name",
    "owner_role",
    "owner_email",
)

# A running turn older than this is treated as dead and closed (the longest turn deadline is 180 s, SPEC D2).
STALE_TURN_S = 300.0
TITLE_MAX = 120

# D141: a trace holds names, hashes, lengths, ids, counts and cost; never owner text.
TRACE_FORBIDDEN_KEYS = frozenset(
    {
        "text",
        "content",
        "message",
        "evidence_text",
        "args",
        "arguments",
        "argument_values",
        "state",
        "jev_state",
        "answer",
        "question",
        "prompt",
    }
)
TRACE_STRING_MAX = 80


class TurnRunning(MiloError):
    """The account already has a running turn (SPEC D99, D131). The web layer answers 409."""

    code = "turn_running"


class Conflict(MiloError):
    code = "conflict"


def user_hex(value: object) -> str:
    """Canonical owner id (32 hex). An unusable value is `PolicyDenied`: no row can belong to it."""
    return _hex(value, PolicyDenied("owner_id"))


def uid(value: object) -> str:
    """Canonical session, turn or other uuid. An unusable value is `NotFound`, like any foreign id."""
    return _hex(value, NotFound())


def _hex(value: object, error: MiloError) -> str:
    text = str(value).strip()
    if len(text) not in (32, 36):
        raise error
    try:
        return uuid.UUID(text).hex
    except ValueError:
        raise error from None


def uuid_text(value: str) -> str:
    """Hyphenated form for SQL parameters."""
    return str(uuid.UUID(value))


def check_choice(value: str, allowed: frozenset[str], what: str) -> str:
    if value not in allowed:
        raise InvalidInput(what)
    return value


def clean_title(title: str | None) -> str | None:
    if title is None:
        return None
    text = " ".join(title.split())
    if not text or len(text) > TITLE_MAX:
        raise InvalidInput("title")
    return text


def check_trace_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Reject owner text in a trace (D141). Strings must be short; known text keys are refused at any depth."""

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                if str(key).lower() in TRACE_FORBIDDEN_KEYS:
                    raise InvalidInput(f"trace_detail:{key}")
                walk(inner, f"{path}.{key}")
        elif isinstance(value, list | tuple):
            for inner in value:
                walk(inner, path)
        elif isinstance(value, str) and len(value) > TRACE_STRING_MAX:
            raise InvalidInput(f"trace_detail:{path}")

    walk(detail, "detail")
    return detail


def check_customer_fields(fields: dict[str, Any]) -> dict[str, Any]:
    unknown = set(fields) - set(CUSTOMER_FIELDS)
    if unknown:
        raise InvalidInput("customer_field")
    return dict(fields)
