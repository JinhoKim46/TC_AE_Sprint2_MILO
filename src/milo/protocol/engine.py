"""The protocol engine (SPEC section 5 step 4, D37, D39; the header of `protocols/discovery.v1.yaml`).

Stateless over the session facts: given `{field_id: {"value", "status", ...}}` it returns the protocol state,
the estimate status, whether a figure may be shown, the assumptions, the missing fields and the next question.
Question text comes from the YAML. Nothing here calls a model, and the model never sets a state.

- `quote_blocked`: a `blocking` field is `conflicting` (highest precedence).
- `quote_ready`: assessment complete, every `quote` field `reported` or `confirmed`, no field `conflicting`.
- `assessment_ready`: every applicable `assessment` field `reported`, `confirmed` or `estimated`.
- `collecting`: otherwise.
Estimate status: collecting -> none, quote_blocked -> blocked, quote_ready -> indicative, assessment_ready ->
scenario (with the quote fields not yet stated listed as assumptions). A figure is allowed only for `scenario`
and `indicative`.
Next question: the first `conflicting` field's `conflict_question` (id `confirm_<field_id>`); otherwise the first
field, in file order, whose condition holds and that has no fact yet (an `unknown` answer counts as a fact).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from milo.protocol.schema import Field, Protocol

ASSESSED = ("reported", "confirmed", "estimated")
STATED = ("reported", "confirmed")
CONFIRM_PREFIX = "confirm_"
ESTIMATE_BY_STATE: dict[str, str | None] = {
    "collecting": None,
    "assessment_ready": "scenario",
    "quote_ready": "indicative",
    "quote_blocked": "blocked",
}
NUMBER_STATUSES = ("scenario", "indicative")

Facts = Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class ProtocolView:
    state: str
    estimate_status: str | None
    number_allowed: bool
    next_question_id: str | None
    next_question: str | None
    assumptions: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    to_confirm: tuple[str, ...] = field(default=())

    def payload(self) -> dict[str, Any]:
        """The JSON-plain form used in turn events and evaluation rows."""
        return {
            "protocol_state": self.state,
            "estimate_status": self.estimate_status,
            "number_allowed": self.number_allowed,
            "next_question_id": self.next_question_id,
            "assumptions": list(self.assumptions),
            "missing": list(self.missing),
            "conflicts": list(self.conflicts),
            "to_confirm": list(self.to_confirm),
        }


class ProtocolEngine:
    def __init__(self, protocol: Protocol) -> None:
        self.protocol = protocol
        self.fields = list(protocol.fields)
        self.by_id = {f.id: f for f in self.fields}
        self.order = {f.id: n for n, f in enumerate(self.fields)}

    # ---- helpers ----

    @staticmethod
    def status(facts: Facts, field_id: str) -> str | None:
        fact = facts.get(field_id)
        return None if fact is None else str(fact.get("status"))

    def applies(self, f: Field, facts: Facts) -> bool:
        """A condition holds when its operand has a fact with the value `equals`. An unknown operand is false."""
        if f.condition is None:
            return True
        operand = facts.get(f.condition.field)
        return operand is not None and operand.get("value") == f.condition.equals

    def question_text(self, question_id: str) -> str | None:
        if question_id.startswith(CONFIRM_PREFIX):
            f = self.by_id.get(question_id[len(CONFIRM_PREFIX) :])
            return None if f is None else (f.conflict_question or f.question)
        f = self.by_id.get(question_id)
        return None if f is None else f.question

    def open_conflict(self, facts: Facts) -> str | None:
        """The field whose conflict question is the current next question, if any (conflicts come first)."""
        for f in self.fields:
            if self.status(facts, f.id) == "conflicting":
                return f.id
        return None

    # ---- the evaluation ----

    def evaluate(self, facts: Facts) -> ProtocolView:
        st = self.status
        conflicts = tuple(f.id for f in self.fields if st(facts, f.id) == "conflicting")
        missing = tuple(
            f.id
            for f in self.fields
            if "assessment" in f.required_for
            and self.applies(f, facts)
            and st(facts, f.id) not in ASSESSED
        )
        assessment = not missing
        quote_open = tuple(
            f.id
            for f in self.fields
            if "quote" in f.required_for and st(facts, f.id) not in STATED
        )
        blocked = any(self.by_id[c].blocking for c in conflicts)
        if blocked:
            state = "quote_blocked"
        elif assessment and not quote_open and not conflicts:
            state = "quote_ready"
        elif assessment:
            state = "assessment_ready"
        else:
            state = "collecting"
        estimate = ESTIMATE_BY_STATE[state]
        if conflicts:
            next_id: str | None = CONFIRM_PREFIX + conflicts[0]
        else:
            next_id = next(
                (
                    f.id
                    for f in self.fields
                    if self.applies(f, facts) and f.id not in facts
                ),
                None,
            )
        to_confirm = tuple(
            f.id
            for f in self.fields
            if st(facts, f.id) == "conflicting"
            or (f.pricing and state != "quote_ready" and f.id in facts)
        )
        return ProtocolView(
            state=state,
            estimate_status=estimate,
            number_allowed=estimate in NUMBER_STATUSES,
            next_question_id=next_id,
            next_question=None if next_id is None else self.question_text(next_id),
            assumptions=quote_open if estimate == "scenario" else (),
            missing=missing,
            conflicts=conflicts,
            to_confirm=to_confirm,
        )
