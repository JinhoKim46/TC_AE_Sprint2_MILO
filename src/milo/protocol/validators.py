"""Per-field checks for the merge (SPEC section 5 step 3): type, range, unit, period and cross-field rules.

Every check is code. A value that fails raises `FactRejected` with a short reason code; the merge turns it into
a `fact_rejected` event and stores nothing. Values are normalised on the way through (numeric strings become
numbers, enum values match case-insensitively, a period such as "a month" becomes `month`), so the model's
formatting does not decide whether a fact is kept.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from milo.protocol.schema import Field, Protocol

MAX_TEXT_CHARS = 500
MAX_LIST_ITEMS = 20
COUNT_UNITS = frozenset({"requests", "people", "sites"})
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PERIOD_WORDS = {
    "day": "day",
    "days": "day",
    "daily": "day",
    "a day": "day",
    "per day": "day",
    "business day": "day",
    "per business day": "day",
    "workday": "day",
    "week": "week",
    "weeks": "week",
    "weekly": "week",
    "a week": "week",
    "per week": "week",
    "month": "month",
    "months": "month",
    "monthly": "month",
    "a month": "month",
    "per month": "month",
}
_TRUE = frozenset({"true", "yes", "y"})
_FALSE = frozenset({"false", "no", "n"})


class FactRejected(ValueError):
    """The value does not fit the protocol field. The message is a short reason code."""

    @property
    def reason(self) -> str:
        return str(self.args[0]) if self.args else "rejected"


def norm_text(text: str) -> str:
    """Whitespace collapsed and trimmed (the D134 normalisation)."""
    return " ".join(str(text).split())


def norm_key(text: Any) -> str:
    return norm_text(str(text)).casefold()


def _number(value: Any) -> float | int:
    if isinstance(value, bool):
        raise FactRejected("expected_number")
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        try:
            num = float(cleaned)
        except ValueError:
            raise FactRejected("expected_number") from None
        return int(num) if num.is_integer() else num
    raise FactRejected("expected_number")


def _unit_ok(given: str | None, expected: str | None) -> bool:
    if given is None or expected is None or not str(given).strip():
        return True
    g = norm_key(given).rstrip("s")
    e = norm_key(expected).rstrip("s")
    return g == e or g in ("job", "call", "request", "booking", "person", "staff", "")


def normalise_period(period: Any, field: Field) -> str | None:
    """The canonical period for a number field with `periods`, or None for a field without periods."""
    if not field.periods:
        return None
    if period is None or not str(period).strip():
        return field.periods[0]
    key = norm_key(period)
    canonical = _PERIOD_WORDS.get(key, key)
    if canonical not in field.periods:
        raise FactRejected("unknown_period")
    return canonical


def per_canonical_period(protocol: Protocol, value: float, period: str | None) -> float:
    """A volume expressed per the field's canonical period (business day), using the protocol's conversions."""
    conv = protocol.conversions
    divisor = {
        None: 1,
        "day": 1,
        "week": conv.business_days_per_week,
        "month": conv.business_days_per_month,
    }.get(period)
    if divisor is None:
        raise FactRejected("unknown_period")
    return value / divisor


def round_for(field: Field, value: float) -> float | int:
    """Converted values are rounded in code: whole numbers for counts, two decimals otherwise."""
    if field.unit in COUNT_UNITS or field.type == "integer":
        return int(round(value))
    return round(value, 2)


def check_value(
    protocol: Protocol, field: Field, value: Any, unit: Any = None, period: Any = None
) -> tuple[Any, str | None, str | None]:
    """Validate and normalise one value against its field. Returns (value, unit, period)."""
    t = field.type
    if t == "string":
        if not isinstance(value, str | int | float) or isinstance(value, bool):
            raise FactRejected("expected_text")
        text = norm_text(str(value))
        if not text:
            raise FactRejected("expected_text")
        if len(text) > MAX_TEXT_CHARS:
            raise FactRejected("too_long")
        value = text
    elif t in ("integer", "number"):
        num = _number(value)
        if t == "integer":
            if int(num) != num:
                raise FactRejected("expected_integer")
            num = int(num)
        if not _unit_ok(unit, field.unit):
            raise FactRejected("unit_mismatch")
        canonical_period = normalise_period(period, field)
        per_day = per_canonical_period(protocol, float(num), canonical_period)
        if field.min is not None and per_day < field.min:
            raise FactRejected("below_min")
        if field.max is not None and per_day > field.max:
            raise FactRejected("above_max")
        return num, field.unit, canonical_period
    elif t == "boolean":
        if isinstance(value, str) and norm_key(value) in _TRUE | _FALSE:
            value = norm_key(value) in _TRUE
        if not isinstance(value, bool):
            raise FactRejected("expected_boolean")
    elif t == "enum":
        allowed = {norm_key(v): v for v in field.values or []}
        key = norm_key(value) if isinstance(value, str) else None
        if key not in allowed:
            raise FactRejected("not_in_values")
        value = allowed[key]
    elif t in ("list", "list_enum"):
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            raise FactRejected("expected_list")
        cleaned = list(dict.fromkeys(norm_text(i) for i in items if norm_text(i)))
        if not cleaned:
            raise FactRejected("empty_list")
        if len(cleaned) > MAX_LIST_ITEMS or any(
            len(i) > MAX_TEXT_CHARS for i in cleaned
        ):
            raise FactRejected("too_long")
        if t == "list_enum":
            allowed = {norm_key(v): v for v in field.values or []}
            if any(norm_key(i) not in allowed for i in cleaned):
                raise FactRejected("not_in_values")
            cleaned = list(dict.fromkeys(allowed[norm_key(i)] for i in cleaned))
        value = cleaned
    return value, None, None


def check_cross_field(
    protocol: Protocol, field: Field, value: Any, facts: Mapping[str, Mapping[str, Any]]
) -> None:
    """Rules that look at other fields. A conditional field is refused when its condition is known to be false."""
    if field.condition is not None:
        operand = facts.get(field.condition.field)
        if (
            operand is not None
            and operand.get("status") in ("reported", "confirmed", "estimated")
            and operand.get("value") != field.condition.equals
        ):
            raise FactRejected("condition_false")
    if field.id == "owner_email" and not _EMAIL.match(str(value)):
        raise FactRejected("not_an_email")
    if field.id == "manual_entry_minutes":
        handling = facts.get("average_handling_minutes")
        if (
            handling is not None
            and isinstance(handling.get("value"), int | float)
            and isinstance(value, int | float)
            and value > 10 * max(float(handling["value"]), 1.0)
        ):
            raise FactRejected("entry_exceeds_handling")
