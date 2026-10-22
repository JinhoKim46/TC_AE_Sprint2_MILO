"""The fact model (SPEC section 5 steps 2 and 3, D37).

- `Extraction` is the extract call's JSON, validated by Pydantic: `{facts: [...], asks_about}`. Items are
  validated one by one, so one malformed item does not discard the others; unknown field ids are dropped by the
  merge and traced.
- `Proposal` is one candidate fact, from the extract call or from the `record_fact` tool.
- `Change` and `Rejection` are the merge's output; each becomes a `fact_recorded` or `fact_rejected` event.

Statuses: `reported` (a new value), `conflicting` (a contradicting value; both kept), `confirmed` (set only by
code, when the owner answers the field's conflict question), `estimated` (reserved for code-derived values) and
`unknown` (the owner said they do not know).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

FactStatus = Literal["reported", "confirmed", "estimated", "unknown", "conflicting"]
AsksAbout = Literal["none", "module", "process", "price"]
RETRIEVE_ON = ("module", "process", "price")
MAX_ITEMS = 40


class ExtractedFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    field_id: str
    value: Any = None
    unit: str | None = None
    period: str | None = None
    evidence_text: str | None = None

    @field_validator("unit", "period", mode="before")
    @classmethod
    def _text_or_none(cls, v: Any) -> Any:
        return None if v is None else str(v)

    @field_validator("field_id")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class ExtractionEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    facts: list[Any] = []
    asks_about: AsksAbout | None = None

    @field_validator("asks_about", mode="before")
    @classmethod
    def _asks(cls, v: Any) -> Any:
        if v is None:
            return None
        key = str(v).strip().lower()
        return key if key in ("none", "module", "process", "price") else "none"


@dataclass
class Extraction:
    facts: list[ExtractedFact]
    asks_about: str | None
    invalid_items: int = 0

    @property
    def retrieve(self) -> bool:
        return self.asks_about in RETRIEVE_ON


def parse_extraction(raw: dict[str, Any]) -> Extraction | None:
    """Validate the parsed JSON object. None when the envelope itself is not the expected shape."""
    try:
        env = ExtractionEnvelope.model_validate(raw)
    except ValidationError:
        return None
    facts: list[ExtractedFact] = []
    invalid = 0
    for item in env.facts[:MAX_ITEMS]:
        try:
            facts.append(ExtractedFact.model_validate(item))
        except ValidationError:
            invalid += 1
    return Extraction(facts, env.asks_about, invalid)


@dataclass
class Proposal:
    field_id: str
    value: Any
    unit: str | None
    period: str | None
    evidence_text: str
    origin: str = "extract"  # or "tool"
    source: str = "owner_message"  # or "upload"


@dataclass
class Change:
    field_id: str
    value: Any
    unit: str | None
    period: str | None
    status: str
    previous_status: str | None
    evidence_text: str
    kind: str  # new, conflict, confirmed, unknown, list_added
    source: str = "owner_message"
    origin: str = "extract"

    def event_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Rejection:
    field_id: str
    reason: str
    origin: str = "extract"

    def event_payload(self) -> dict[str, Any]:
        return asdict(self)
