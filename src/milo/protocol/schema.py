"""Pydantic loader for `protocols/discovery.v1.yaml` (SPEC 5.1, D36).

The committed file is authoritative. Every key it uses is declared here; unknown keys fail the load. An unknown
field referenced in a condition fails the load.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

DEFAULT_PATH = Path("protocols/discovery.v1.yaml")

FieldType = Literal[
    "string", "integer", "number", "boolean", "enum", "list", "list_enum"
]
RequiredFor = Literal["assessment", "quote"]


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    equals: Any


class Field(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    group: str
    type: FieldType
    question: str
    required_for: list[RequiredFor] = []
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    values: list[str] | None = None
    periods: list[str] | None = None
    pricing: bool = False
    blocking: bool = False
    allow_unknown: bool = False
    pii: bool = False
    condition: Condition | None = None
    conflict_question: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Field:
        if self.type in ("enum", "list_enum") and not self.values:
            raise ValueError(f"field {self.id}: type {self.type} needs values")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"field {self.id}: min above max")
        return self


class Conversions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_days_per_month: int
    business_days_per_week: int


class Protocol(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    updated: str
    states: list[str]
    groups: dict[str, str]
    conversions: Conversions
    fields: list[Field]

    @model_validator(mode="after")
    def _check(self) -> Protocol:
        ids = [f.id for f in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate field ids")
        known = set(ids)
        for f in self.fields:
            if f.group not in self.groups:
                raise ValueError(f"field {f.id}: unknown group {f.group}")
            if f.condition is not None and f.condition.field not in known:
                raise ValueError(
                    f"field {f.id}: condition names unknown field {f.condition.field}"
                )
        return self

    def field_by_id(self, field_id: str) -> Field:
        for f in self.fields:
            if f.id == field_id:
                return f
        raise KeyError(field_id)

    @property
    def pricing_field_ids(self) -> frozenset[str]:
        return frozenset(f.id for f in self.fields if f.pricing)


def load_protocol(path: Path | str = DEFAULT_PATH) -> Protocol:
    with Path(path).open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Protocol.model_validate(raw)
