"""Approval lifecycle for destructive tools (SPEC section 8, D48), independent of the model.

`ApprovalService` sits on an `ApprovalStore` and a tools backend. `App.resume_approval` keeps the model-driven
path (it needs the saved messages for the follow-up call); this service is the no-model path: the owner's
`/withdraw/<submission_id>` button and any caller that must resolve an approval without a chat model.

Rules enforced here: only the owning session resolves an approval (anyone else gets `not_found`); an expired row
runs nothing; a row is used once; the bound argument hash is rechecked before the tool runs; the tool runs with
the identity bound to the row, never with a value from the arguments; the policy decides the same way as in the
loop (taint low, since no model text is involved).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from milo.security.policy import decide
from milo.store.memory import args_hash
from milo.tools.args import clean_args
from milo.types import ApprovalStore, ToolsBackend

WITHDRAW = "withdraw_submission"


@dataclass
class ApprovalOutcome:
    status: (
        str  # not_found, expired, already_handled, kept, used, denied, failed, pending
    )
    approval_id: str | None = None
    output: Any = None
    ok: bool = False
    executed: bool = False


class ApprovalService:
    def __init__(self, tools: ToolsBackend, approvals: ApprovalStore) -> None:
        self.tools = tools
        self.approvals = approvals

    def request(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        user_id: str,
        scope: str,
        turn_id: str = "",
    ) -> ApprovalOutcome:
        """Ask the policy about a call. A destructive call at taint low gets a pending approval; nothing runs."""
        spec = next((s for s in self.tools.specs() if s.name == tool_name), None)
        if spec is None:
            return ApprovalOutcome("denied")
        cleaned = clean_args(args)
        decision = decide(
            spec, cleaned, "low", getattr(self.tools, "pricing_fields", ())
        )
        if decision.decision == "denied":
            return ApprovalOutcome("denied")
        if decision.decision == "allowed":
            return ApprovalOutcome("allowed")
        approval_id = self.approvals.create(
            scope=scope,
            owner_id=user_id,
            turn_id=turn_id,
            tool_name=tool_name,
            args=cleaned,
        )
        return ApprovalOutcome("pending", approval_id)

    def resolve(self, approval_id: str, decision: str, user_id: str) -> ApprovalOutcome:
        """`approve` runs the bound tool once; `keep` runs nothing. No model call either way."""
        row = self.approvals.get(approval_id, user_id)
        if row is None:
            return ApprovalOutcome("not_found", approval_id)
        if row["status"] == "expired":
            return ApprovalOutcome("expired", approval_id)
        if row["status"] != "pending":
            return ApprovalOutcome("already_handled", approval_id)
        if decision == "keep":
            self.approvals.set_status(approval_id, user_id, "kept")
            return ApprovalOutcome("kept", approval_id)
        if decision != "approve":
            return ApprovalOutcome("denied", approval_id)
        if row.get("args_hash") not in (None, args_hash(row["args"])):
            return ApprovalOutcome("denied", approval_id)
        self.approvals.set_status(approval_id, user_id, "approved")
        try:
            output = self.tools.call(
                row["tool_name"], dict(row["args"]), user_id=row["owner_id"]
            )
            ok = True
        except Exception:  # a tool failure is a result, never a traceback (SPEC D23)
            output, ok = {"error": "tool_failed"}, False
        self.approvals.set_status(approval_id, user_id, "used")
        return ApprovalOutcome(
            "used" if ok else "failed", approval_id, output, ok, executed=True
        )

    def withdraw(
        self, submission_id: int, *, user_id: str, scope: str = "withdraw"
    ) -> ApprovalOutcome:
        """The `/withdraw/<submission_id>` route: the owner's confirmed click is the approval (SPEC section 8)."""
        pending = self.request(
            WITHDRAW, {"submission_id": submission_id}, user_id=user_id, scope=scope
        )
        if pending.status != "pending" or pending.approval_id is None:
            return pending
        return self.resolve(pending.approval_id, "approve", user_id)
