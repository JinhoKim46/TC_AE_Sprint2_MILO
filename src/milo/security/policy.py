"""Tool policy: risk times taint (SPEC section 8). S0 seam; the security milestone replaces the body.

- read: allowed.
- write: allowed at taint low; at taint high allowed unless the tool is `record_fact` on a pricing field.
- destructive: needs approval at taint low; denied at taint high.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from milo.types import PolicyDecision, Taint, ToolSpec


@dataclass(frozen=True)
class Decision:
    decision: PolicyDecision
    reason: str


def decide(
    spec: ToolSpec,
    args: dict[str, Any],
    taint: Taint,
    pricing_fields: Collection[str] = (),
) -> Decision:
    if spec.risk == "read":
        return Decision("allowed", "read tool")
    if spec.risk == "write":
        if (
            taint == "high"
            and spec.name == "record_fact"
            and args.get("field_id") in pricing_fields
        ):
            return Decision("denied", "pricing field with high taint")
        return Decision("allowed", "write tool")
    if spec.risk == "destructive":
        if taint == "high":
            return Decision("denied", "destructive tool with high taint")
        return Decision("needs_approval", "destructive tool")
    return Decision("denied", "unknown risk tier")
