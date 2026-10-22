"""Session facts and submissions behind the tools (SPEC D70, D74).

`SpecStore` is what `MiloBackend` needs from storage. `MemorySpecStore` is the in-memory implementation that
tests and contract mode use; it applies the owner filter in code, as row-level security does in Postgres. The
store role and the discovery chain (M5b) supply a persistent implementation with the same four methods.

`check_fact` and the merge in `MemorySpecStore.put_fact` are a minimal stand-in for the chain's merge step
(SPEC section 5 step 3): type, range, unit and period checks; a new value is `reported`; a differing value
becomes `conflicting` with the earlier value kept; list fields accumulate. `confirmed` is never set here, and
the verbatim-evidence rule (D134) belongs to the chain, which has the owner message.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from milo.protocol.schema import Field, Protocol as ProtocolFile

OBJECTS_PATH = Path(__file__).with_name("objects.json")


def load_objects(path: Path | str = OBJECTS_PATH) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


class FactRejected(ValueError):
    """The value does not fit the protocol field; the message is a short reason code."""


def _is_number(v: object) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def check_fact(
    field: Field, value: Any, unit: str | None, period: str | None
) -> tuple[Any, str | None, str | None]:
    """Validate and normalise one fact against its protocol field. Returns (value, unit, period)."""
    t = field.type
    if t == "string":
        if not isinstance(value, str) or not value.strip():
            raise FactRejected("expected_text")
        value = value.strip()
    elif t in ("integer", "number"):
        if not _is_number(value):
            raise FactRejected("expected_number")
        if t == "integer":
            if int(value) != value:
                raise FactRejected("expected_integer")
            value = int(value)
        if field.min is not None and value < field.min:
            raise FactRejected("below_min")
        if field.max is not None and value > field.max:
            raise FactRejected("above_max")
    elif t == "boolean":
        if not isinstance(value, bool):
            raise FactRejected("expected_boolean")
    elif t == "enum":
        if value not in (field.values or []):
            raise FactRejected("not_in_values")
    elif t in ("list", "list_enum"):
        if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
            raise FactRejected("expected_list")
        value = [i.strip() for i in value if i.strip()]
        if not value:
            raise FactRejected("empty_list")
        if t == "list_enum" and any(i not in (field.values or []) for i in value):
            raise FactRejected("not_in_values")
    if t in ("integer", "number"):
        if field.unit is not None and unit not in (None, field.unit):
            raise FactRejected("unit_mismatch")
        unit = field.unit
        if field.periods:
            if period is None:
                period = field.periods[0]
            if period not in field.periods:
                raise FactRejected("unknown_period")
        else:
            period = None
    else:
        unit, period = None, None
    return value, unit, period


class SpecStore(Protocol):
    def facts(self, owner_id: str) -> list[dict[str, Any]]: ...

    def put_fact(
        self,
        owner_id: str,
        field: Field,
        value: Any,
        unit: str | None,
        period: str | None,
        evidence_text: str,
    ) -> tuple[dict[str, Any], bool]:
        """Merge one checked fact. Returns the stored row and whether anything changed."""
        ...

    def withdraw(self, owner_id: str, submission_id: int) -> str:
        """`withdrawn`, `already_withdrawn` or `not_found` (also for another owner's id, SPEC D65)."""
        ...


class MemorySpecStore:
    def __init__(
        self, protocol: ProtocolFile, objects: dict[str, Any] | None = None
    ) -> None:
        self._protocol = protocol
        self._order = {f.id: n for n, f in enumerate(protocol.fields)}
        self._facts: dict[str, dict[str, dict[str, Any]]] = {}
        data = objects if objects is not None else load_objects()
        self._submissions: dict[int, dict[str, Any]] = {
            int(k): dict(v) for k, v in data.get("submissions", {}).items()
        }

    def facts(self, owner_id: str) -> list[dict[str, Any]]:
        rows = self._facts.get(owner_id, {})
        ordered = sorted(rows.values(), key=lambda r: self._order[r["field_id"]])
        return [json.loads(json.dumps(r)) for r in ordered]

    def put_fact(
        self,
        owner_id: str,
        field: Field,
        value: Any,
        unit: str | None,
        period: str | None,
        evidence_text: str,
    ) -> tuple[dict[str, Any], bool]:
        rows = self._facts.setdefault(owner_id, {})
        old = rows.get(field.id)
        new = {
            "field_id": field.id,
            "value": value,
            "unit": unit,
            "period": period,
            "status": "reported",
            "evidence_text": evidence_text,
        }
        if old is None or old["status"] == "unknown":
            rows[field.id] = new
            return dict(new), True
        if field.type in ("list", "list_enum"):
            merged = list(old["value"])
            merged += [i for i in value if i not in merged]
            if merged == old["value"]:
                return dict(old), False
            old.update(value=merged, evidence_text=evidence_text)
            return dict(old), True
        if old["value"] == value and old["period"] == period:
            return dict(old), False
        alternatives = [*old.get("alternatives", []), old["value"]]
        new.update(status="conflicting", alternatives=alternatives)
        rows[field.id] = new
        return dict(new), True

    def withdraw(self, owner_id: str, submission_id: int) -> str:
        row = self._submissions.get(submission_id)
        if row is None or row["owner_id"] != owner_id:
            return "not_found"
        if row["status"] == "withdrawn":
            return "already_withdrawn"
        row["status"] = "withdrawn"
        return "withdrawn"

    def submission_status(self, submission_id: int) -> str | None:
        """For tests and the attack runner: the stored status, whoever the owner is."""
        row = self._submissions.get(submission_id)
        return None if row is None else str(row["status"])
