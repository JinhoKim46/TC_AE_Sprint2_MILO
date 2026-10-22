"""The merge (SPEC section 5 step 3, D22, D37, D134). Code only; the model never sets a status.

`merge` takes the session's current facts and the turn's proposals and returns the changes, the rejections and
the new fact map. Proposals are applied in protocol field order, so a condition's operand is merged before the
field it gates. For each proposal:

1. An unknown field id is dropped (`unknown_field`).
2. D134: the evidence must be, after whitespace normalisation, a substring of the owner message or the upload
   text of the same turn; otherwise the fact is discarded (`evidence_not_verbatim`).
3. D22: at taint high a pricing field is refused (`policy_denied`), on the extract path as on the tool path.
4. A null value means the owner does not know: status `unknown` (never over a stated value).
5. Type, range, unit and period checks, then cross-field rules (`protocol.validators`).
6. A new value is `reported`. A value that contradicts a stated one makes the field `conflicting`, with both
   values kept (`value` holds the list of kept values). List fields accumulate instead. When the field is the
   open conflict question (the engine's next question at the start of the turn) and the owner gives a value,
   the field becomes `confirmed`; a weekly or monthly volume is converted to the canonical period in code.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from milo.facts.model import Change, Proposal, Rejection
from milo.protocol.schema import Field, Protocol
from milo.protocol.validators import (
    FactRejected,
    check_cross_field,
    check_value,
    norm_key,
    norm_text,
    per_canonical_period,
    round_for,
)
from milo.types import Taint

SAME_VOLUME_TOLERANCE = (
    0.15  # two volumes within 15 percent per business day are the same statement
)


@dataclass
class MergeResult:
    changes: list[Change] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    facts: dict[str, dict[str, Any]] = field(default_factory=dict)


def evidence_is_verbatim(evidence: str | None, sources: Sequence[str]) -> bool:
    """D134: non-empty and, after whitespace normalisation, a substring of one of the turn's texts."""
    ev = norm_text(evidence or "")
    return bool(ev) and any(ev in norm_text(s) for s in sources if s)


def _same(
    protocol: Protocol, f: Field, old: Mapping[str, Any], value: Any, period: str | None
) -> bool:
    a = old.get("value")
    if f.type in ("integer", "number"):
        if not isinstance(a, int | float) or isinstance(a, bool):
            return False
        if f.periods:
            x = per_canonical_period(protocol, float(a), old.get("period"))
            y = per_canonical_period(protocol, float(value), period)
            return abs(x - y) <= SAME_VOLUME_TOLERANCE * max(abs(x), abs(y), 1e-9)
        return float(a) == float(value)
    if f.type == "string" and isinstance(a, str) and isinstance(value, str):
        ka, kb = norm_key(a), norm_key(value)
        return ka == kb or ka in kb or kb in ka
    return bool(a == value)


def _in_alternatives(f: Field, alternatives: Any, value: Any) -> bool:
    items = alternatives if isinstance(alternatives, list) else [alternatives]
    if f.type == "string":
        return any(norm_key(i) == norm_key(value) for i in items)
    return value in items


def merge(
    protocol: Protocol,
    facts: Mapping[str, Mapping[str, Any]],
    proposals: Sequence[Proposal],
    *,
    sources: Sequence[str],
    taint: Taint,
    confirming: str | None = None,
) -> MergeResult:
    by_id = {f.id: f for f in protocol.fields}
    order = {f.id: n for n, f in enumerate(protocol.fields)}
    work: dict[str, dict[str, Any]] = {
        k: dict(copy.deepcopy(v)) for k, v in facts.items()
    }
    out = MergeResult()

    def reject(p: Proposal, reason: str) -> None:
        out.rejections.append(Rejection(p.field_id, reason, p.origin))

    def store(
        p: Proposal,
        f: Field,
        value: Any,
        unit: str | None,
        period: str | None,
        status: str,
        kind: str,
        prev: str | None,
    ) -> None:
        work[f.id] = {
            "field_id": f.id,
            "value": value,
            "unit": unit,
            "period": period,
            "status": status,
            "evidence_text": norm_text(p.evidence_text),
            "source": p.source,
        }
        out.changes.append(
            Change(
                f.id,
                copy.deepcopy(value),
                unit,
                period,
                status,
                prev,
                norm_text(p.evidence_text),
                kind,
                p.source,
                p.origin,
            )
        )

    ranked = sorted(proposals, key=lambda p: order.get(p.field_id, len(order)))
    for p in ranked:
        f = by_id.get(p.field_id)
        if f is None:
            reject(p, "unknown_field")
            continue
        if not evidence_is_verbatim(p.evidence_text, sources):
            reject(p, "evidence_not_verbatim")
            continue
        in_message = bool(sources) and evidence_is_verbatim(
            p.evidence_text, sources[:1]
        )
        p = replace(p, source="owner_message" if in_message else "upload")
        if taint == "high" and f.pricing:
            reject(p, "policy_denied")
            continue
        old = work.get(f.id)
        prev = None if old is None else str(old.get("status"))
        if p.value is None:
            if old is None:
                store(p, f, None, None, None, "unknown", "unknown", prev)
            elif prev != "unknown":
                reject(p, "unknown_after_value")
            continue
        try:
            value, unit, period = check_value(protocol, f, p.value, p.unit, p.period)
            check_cross_field(protocol, f, value, work)
        except FactRejected as exc:
            reject(p, exc.reason)
            continue
        if old is None or prev == "unknown":
            store(p, f, value, unit, period, "reported", "new", prev)
        elif prev == "conflicting":
            if confirming == f.id:
                if taint == "high":
                    reject(p, "policy_denied")
                    continue
                if f.periods and f.type in ("integer", "number"):
                    value = round_for(
                        f, per_canonical_period(protocol, float(value), period)
                    )
                    period = f.periods[0]
                store(p, f, value, unit, period, "confirmed", "confirmed", prev)
            elif not _in_alternatives(f, old.get("value"), value):
                kept = (
                    list(old["value"])
                    if isinstance(old.get("value"), list)
                    else [old.get("value")]
                )
                store(p, f, [*kept, value], None, None, "conflicting", "conflict", prev)
        elif f.type in ("list", "list_enum"):
            current = list(old.get("value") or [])
            known = {norm_key(i) for i in current}
            added = [i for i in value if norm_key(i) not in known]
            if added:
                store(p, f, current + added, None, None, "reported", "list_added", prev)
        elif not _same(protocol, f, old, value, period):
            store(
                p,
                f,
                [old.get("value"), value],
                None,
                None,
                "conflicting",
                "conflict",
                prev,
            )
    out.facts = work
    return out
