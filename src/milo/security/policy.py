"""Tool policy: risk times taint (SPEC section 8, D22; M4 body).

- read: allowed.
- write: allowed at taint low; at taint high allowed unless the tool is `record_fact` on a pricing field.
- destructive: needs approval at taint low; denied at taint high.
Anything else (an unknown risk tier, a malformed spec) is denied: the policy fails closed.

`decide` is a pure function; the loop traces its decision. `decide_on_approve` is the check that a caller which
resolves an approval must run again before it runs the bound tool (see `docs/requests/m4.md`).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from milo.types import PolicyDecision, Taint, ToolSpec

RECORD_FACT = "record_fact"


@dataclass(frozen=True)
class Decision:
    decision: PolicyDecision
    reason: str


def _is_pricing_write(
    spec: ToolSpec, args: dict[str, Any], pricing_fields: Collection[str]
) -> bool:
    """A write that sets a field the pricing engine reads. An unreadable `field_id` counts as pricing."""
    if spec.name != RECORD_FACT:
        return False
    field_id = args.get("field_id") if isinstance(args, dict) else None
    if not isinstance(field_id, str):
        return True
    return field_id in pricing_fields


def decide(
    spec: ToolSpec,
    args: dict[str, Any],
    taint: Taint,
    pricing_fields: Collection[str] = (),
) -> Decision:
    if taint not in ("low", "high"):
        return Decision("denied", "unknown taint")
    if spec.risk == "read":
        return Decision("allowed", "read tool")
    if spec.risk == "write":
        if taint == "high" and _is_pricing_write(spec, args, pricing_fields):
            return Decision("denied", "pricing field with high taint")
        return Decision("allowed", "write tool")
    if spec.risk == "destructive":
        if taint == "high":
            return Decision("denied", "destructive tool with high taint")
        return Decision("needs_approval", "destructive tool")
    return Decision("denied", "unknown risk tier")


def decide_on_approve(
    spec: ToolSpec | None,
    args: dict[str, Any],
    pricing_fields: Collection[str] = (),
    taint: Taint = "low",
) -> Decision:
    """The policy again, at the moment an owner approves a stored call.

    An approval row exists only because the policy said `needs_approval` at taint low, and the owner's approve
    click is that approval, so the row is judged at taint low unless the caller passes the session's current taint. The call may run when the tool still exists and the
    policy does not deny it now (a tool removed, or a risk tier or pricing field changed since the row was made).
    The answer is `allowed` when the call may run, `denied` otherwise.
    """
    if spec is None:
        return Decision("denied", "unknown tool at approve time")
    now = decide(spec, args, taint, pricing_fields)
    if now.decision == "denied":
        return Decision("denied", f"denied at approve time: {now.reason}")
    return Decision("allowed", "approved by the owner")
