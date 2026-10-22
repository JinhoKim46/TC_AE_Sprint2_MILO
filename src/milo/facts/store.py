"""Session facts over the repositories (SPEC section 5 step 3, section 6 `record_fact`, D70, D134).

`SessionFacts` is the one place facts are read and written in discovery mode. The chain's merge and the
`record_fact` tool both go through `merge_and_store`, so there is a single merge path. It also implements the
`SpecStore` protocol that `MiloBackend` needs (`facts`, `put_fact`, `withdraw`), keyed by the account: the
chain binds the account's running turn (session, owner text, upload text, taint, message id) before the reply
phase and releases it after, and one running turn per account (D131) makes that binding unambiguous. Outside a
running turn `put_fact` refuses, because the evidence cannot be checked against the turn's text (D134).

The facts table holds no evidence text; the quote travels in the `fact_recorded` event and is kept here, in
process, for `get_session_spec`. The evidence table gets a row per change with the text's hash (D141).
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from milo.errors import MiloError, NotFound
from milo.facts.merge import MergeResult, merge
from milo.facts.model import Proposal, Rejection
from milo.protocol.engine import ProtocolEngine
from milo.protocol.schema import Field, Protocol
from milo.store.common import user_hex
from milo.trace import text_hash
from milo.types import Taint

OWNER_NAMESPACE = uuid.UUID("6f1c0c3e-6a52-4f55-9d55-6d696c6f0001")


def owner_hex(user_id: str) -> str:
    """The store's owner id for a caller id: a uuid stays itself; any other id maps to a stable uuid5."""
    try:
        return user_hex(user_id)
    except MiloError:
        return uuid.uuid5(OWNER_NAMESPACE, f"milo-owner:{user_id}").hex


@dataclass
class TurnBinding:
    owner: str
    session_id: str
    sources: list[str]
    taint: Taint
    message_id: int | None
    confirming: str | None
    upload: str | None = None
    rejections: list[Rejection] = field(default_factory=list)
    changes: list[Any] = field(default_factory=list)


class SessionFacts:
    def __init__(
        self,
        repos: Any,
        protocol: Protocol,
        *,
        on_change: Callable[[str, MergeResult], None] | None = None,
    ) -> None:
        self.repos = repos
        self.protocol = protocol
        self.engine = ProtocolEngine(protocol)
        self._quotes: dict[tuple[str, str, str], str] = {}
        self._bound: dict[str, TurnBinding] = {}
        self._lock = threading.Lock()
        self.on_change = on_change

    # ---- reading ----

    def load(self, owner: str, session_id: str) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in self.repos.facts.for_session(owner, session_id):
            fid = row["field_id"]
            out[fid] = {
                "field_id": fid,
                "value": row.get("value_json"),
                "unit": row.get("unit"),
                "period": row.get("period"),
                "status": row.get("status"),
                "evidence_text": self._quotes.get((owner, session_id, fid), ""),
                "source_message_id": row.get("source_message_id"),
            }
        return out

    def as_list(self, owner: str, session_id: str) -> list[dict[str, Any]]:
        """Facts in protocol order, the shape the estimator and `get_session_spec` read."""
        facts = self.load(owner, session_id)
        return [dict(facts[f.id]) for f in self.protocol.fields if f.id in facts]

    # ---- writing (the one merge path) ----

    def merge_and_store(
        self,
        owner: str,
        session_id: str,
        proposals: Sequence[Proposal],
        *,
        sources: Sequence[str],
        taint: Taint,
        confirming: str | None,
        message_id: int | None,
        upload: str | None = None,
    ) -> MergeResult:
        with self._lock:
            current = self.load(owner, session_id)
            result = merge(
                self.protocol,
                current,
                proposals,
                sources=sources,
                taint=taint,
                confirming=confirming,
            )
            for ch in result.changes:
                self.repos.facts.upsert(
                    owner,
                    session_id,
                    ch.field_id,
                    value=ch.value,
                    status=ch.status,
                    unit=ch.unit,
                    period=ch.period,
                    source_message_id=message_id,
                )
                self._quotes[(owner, session_id, ch.field_id)] = ch.evidence_text
                self.repos.evidence.add(
                    owner,
                    session_id,
                    "uploaded_document"
                    if ch.source == "upload"
                    else "customer_statement",
                    ref=ch.field_id,
                    text_hash=text_hash(ch.evidence_text),
                    message_id=message_id,
                )
        if self.on_change is not None:
            self.on_change(owner, result)
        return result

    # ---- the binding of an account's running turn ----

    def bind(self, user_id: str, binding: TurnBinding) -> None:
        self._bound[user_id] = binding

    def release(self, user_id: str) -> TurnBinding | None:
        return self._bound.pop(user_id, None)

    def binding(self, user_id: str) -> TurnBinding | None:
        return self._bound.get(user_id)

    # ---- SpecStore protocol for MiloBackend (tools/state.py) ----

    def facts(self, owner_id: str) -> list[dict[str, Any]]:
        b = self._bound.get(owner_id)
        if b is None:
            return []
        return self.as_list(b.owner, b.session_id)

    def put_fact(
        self,
        owner_id: str,
        field: Field,
        value: Any,
        unit: str | None,
        period: str | None,
        evidence_text: str,
    ) -> tuple[dict[str, Any], bool]:
        b = self._bound.get(owner_id)
        if b is None:
            return {"status": "rejected", "reason": "no_running_turn"}, False
        result = self.merge_and_store(
            b.owner,
            b.session_id,
            [Proposal(field.id, value, unit, period, evidence_text, origin="tool")],
            sources=b.sources,
            taint=b.taint,
            confirming=b.confirming,
            message_id=b.message_id,
            upload=b.upload,
        )
        b.rejections.extend(result.rejections)
        b.changes.extend(result.changes)
        if result.rejections:
            return {"status": "rejected", "reason": result.rejections[0].reason}, False
        row = result.facts.get(field.id, {"status": "unchanged"})
        return {"status": row.get("status")}, bool(result.changes)

    def withdraw(self, owner_id: str, submission_id: int) -> str:
        owner = owner_hex(owner_id)
        try:
            row = self.repos.submissions.get(owner, int(submission_id))
        except (NotFound, MiloError, ValueError):
            return "not_found"
        if row.get("status") == "withdrawn":
            return "already_withdrawn"
        self.repos.submissions.set_status(owner, int(submission_id), "withdrawn")
        return "withdrawn"


def facts_view(facts: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """A JSON-plain copy without the quotes (for events and traces that must not carry owner text)."""
    return {
        k: {"value": v.get("value"), "status": v.get("status")}
        for k, v in facts.items()
    }
