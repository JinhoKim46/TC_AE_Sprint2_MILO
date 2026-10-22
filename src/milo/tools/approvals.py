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

import threading
from dataclasses import dataclass
from typing import Any

from milo.security.policy import decide, decide_on_approve
from milo.store.memory import args_hash
from milo.tools.args import clean_args
from milo.types import ApprovalStore, Taint, ToolsBackend

WITHDRAW = "withdraw_submission"
# One claim at a time: the status check and the pending -> approved write are a single step (compare-and-set).
_CLAIM_LOCK = threading.Lock()


@dataclass
class ApprovalOutcome:
    status: str  # not_found, expired, already_resolved, denied_at_approve, kept, used, denied, failed, pending
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

    def _claim(self, approval_id: str, user_id: str, new: str = "approved") -> bool:
        """Compare-and-set: `pending` -> `new` succeeds for exactly one caller."""
        with _CLAIM_LOCK:
            row = self.approvals.get(approval_id, user_id)
            if row is None or row["status"] != "pending":
                return False
            self.approvals.set_status(approval_id, user_id, new)
            return True

    def resolve(
        self,
        approval_id: str,
        decision: str,
        user_id: str,
        taint: Taint = "low",
    ) -> ApprovalOutcome:
        """`approve` runs the bound tool once; `keep` runs nothing. No model call either way.

        `taint` is the session's taint now (default low: no model text is involved). The policy is asked again with
        the stored arguments before the tool runs; a denial marks the row `denied_at_approve` and runs nothing.
        """
        row = self.approvals.get(approval_id, user_id)
        if row is None:
            return ApprovalOutcome("not_found", approval_id)
        if row["status"] == "expired":
            return ApprovalOutcome("expired", approval_id)
        if row["status"] != "pending":
            return ApprovalOutcome("already_resolved", approval_id)
        if decision == "keep":
            if not self._claim(approval_id, user_id, "kept"):
                return ApprovalOutcome("already_resolved", approval_id)
            return ApprovalOutcome("kept", approval_id)
        if decision != "approve":
            return ApprovalOutcome("denied", approval_id)
        if row.get("args_hash") not in (None, args_hash(row["args"])):
            return ApprovalOutcome("denied", approval_id)
        if not self._claim(approval_id, user_id):
            return ApprovalOutcome("already_resolved", approval_id)
        spec = next((s for s in self.tools.specs() if s.name == row["tool_name"]), None)
        pricing = getattr(self.tools, "pricing_fields", ())
        verdict = decide_on_approve(spec, clean_args(row["args"]), pricing, taint)
        if verdict.decision == "denied":
            self.approvals.set_status(approval_id, user_id, "denied_at_approve")
            return ApprovalOutcome("denied_at_approve", approval_id)
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
