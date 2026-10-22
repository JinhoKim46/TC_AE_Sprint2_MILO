"""The cost estimator (SPEC section 5 step 7, D38, D39). Prices are arithmetic.

The model supplies module ids and nothing else. Volume and integrations come from the session facts; every
figure is a rate-card constant times a source field, and each line names its sources. `parse_model_args`
refuses any other key, so a number from the model cannot reach the calculation.

Formula (KB article "How we price"): monthly = sum(base_monthly[m] x tier_factor) + sum(surcharge[i] for each
distinct integration i); setup = per_module x number of modules; low, base and high apply the range factors to
each of the two totals. Figures are rounded to cents, half up, after the totals are formed from the rounded lines.

Status (D39, protocol header): `blocked` (a blocking field is conflicting; no number), `unavailable` (the
assessment fields are not complete, or the volume is above the last tier; no number), `scenario` (assessment
complete, a quote field is missing, estimated or conflicting; assumptions listed), `indicative` (every quote
field reported or confirmed, no conflict).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from milo.pricing.rate_card import dec, known_integrations, money, volume_factor
from milo.pricing.schema import RateCard
from milo.protocol.schema import Field, Protocol

NUMBER_STATUSES = ("scenario", "indicative")
ASSESSED = ("reported", "confirmed", "estimated")
STATED = ("reported", "confirmed")
DEFAULT_INTEGRATION = "csv-export"


class ModelSuppliedNumber(ValueError):
    """The model passed something other than `module_ids` to the estimator."""


class EstimateInputError(ValueError):
    """Module ids or fact values that the rate card cannot price."""


@dataclass(frozen=True)
class Range:
    low: float
    base: float
    high: float


@dataclass(frozen=True)
class Line:
    label: str
    amount_usd: float
    sources: tuple[str, ...]


@dataclass(frozen=True)
class Estimate:
    status: str
    module_ids: tuple[str, ...]
    assumptions: tuple[str, ...]
    rate_card_version: str
    currency: str
    daily_volume: float | None = None
    volume_factor: float | None = None
    integrations: tuple[str, ...] = ()
    lines: tuple[Line, ...] = ()
    monthly: Range | None = None
    setup: Range | None = None
    notes: tuple[str, ...] = field(default=())

    def tool_output(self) -> dict[str, Any]:
        """What the model sees: status, module ids and assumptions, never a figure (SPEC section 6)."""
        return {
            "status": self.status,
            "module_ids": list(self.module_ids),
            "assumptions": list(self.assumptions),
        }

    def event_payload(self) -> dict[str, Any]:
        """The `estimate_updated` event body for the panel (SPEC section 5 step 7)."""
        out = self.tool_output()
        out.update(
            rate_card_version=self.rate_card_version,
            currency=self.currency,
            daily_volume=self.daily_volume,
            volume_factor=self.volume_factor,
            integrations=list(self.integrations),
            lines=[
                {"label": x.label, "amount": x.amount_usd, "sources": list(x.sources)}
                for x in self.lines
            ],
            monthly=None if self.monthly is None else vars(self.monthly).copy(),
            setup=None if self.setup is None else vars(self.setup).copy(),
            notes=list(self.notes),
        )
        return out


def parse_model_args(args: Mapping[str, Any]) -> tuple[str, ...]:
    """Module ids from the model's arguments. Any other key, or a non-string id, is refused."""
    extra = sorted(set(args) - {"module_ids"})
    if extra:
        raise ModelSuppliedNumber(
            "estimate_cost accepts module_ids only; refused: " + ", ".join(extra)
        )
    ids = args.get("module_ids")
    if not isinstance(ids, Sequence) or isinstance(ids, str | bytes) or not ids:
        raise EstimateInputError("module_ids must be a non-empty list")
    if not all(isinstance(i, str) for i in ids):
        raise EstimateInputError("module_ids must be strings")
    return tuple(ids)


def check_modules(card: RateCard, module_ids: Sequence[str]) -> tuple[str, ...]:
    if not module_ids:
        raise EstimateInputError("no modules")
    if len(set(module_ids)) != len(module_ids):
        raise EstimateInputError("duplicate module ids")
    if len(module_ids) > card.max_modules_per_estimate:
        raise EstimateInputError(
            f"at most {card.max_modules_per_estimate} modules per estimate"
        )
    unknown = [m for m in module_ids if m not in card.base_monthly_usd]
    if unknown:
        raise EstimateInputError("unknown module: " + ", ".join(unknown))
    return tuple(module_ids)


def _range(card: RateCard, total: Decimal) -> Range:
    r = card.range
    return Range(
        money(total * dec(r.low_factor)),
        money(total * dec(r.base_factor)),
        money(total * dec(r.high_factor)),
    )


def price(
    card: RateCard,
    module_ids: Sequence[str],
    *,
    daily_volume: float,
    integrations: Sequence[str],
    status: str,
    assumptions: Sequence[str] = (),
) -> Estimate:
    """The arithmetic. `status` and `assumptions` come from `plan_estimate`; no figure comes from the model."""
    modules = check_modules(card, module_ids)
    if status not in NUMBER_STATUSES:
        raise EstimateInputError(f"status {status} carries no figures")
    unknown = [i for i in integrations if i not in known_integrations(card)]
    if unknown:
        raise EstimateInputError("unknown integration: " + ", ".join(unknown))
    factor = volume_factor(card, daily_volume)
    if factor is None:
        return Estimate(
            "unavailable",
            modules,
            (*assumptions, "volume_above_rate_card"),
            card.version,
            card.currency,
            daily_volume=float(daily_volume),
            notes=("above the last volume tier a person prices it",),
        )
    distinct = tuple(dict.fromkeys(integrations))
    lines: list[Line] = []
    monthly = Decimal(0)
    for m in modules:
        amount = dec(money(dec(card.base_monthly_usd[m]) * dec(factor)))
        monthly += amount
        lines.append(
            Line(
                f"{m} monthly",
                float(amount),
                (
                    f"rate_card.base_monthly_usd.{m}",
                    "rate_card.volume_tiers_daily_requests",
                    "fact.daily_request_volume",
                ),
            )
        )
    for i in distinct:
        amount = dec(card.integration_surcharge_monthly_usd[i])
        monthly += amount
        lines.append(
            Line(
                f"{i} surcharge monthly",
                float(amount),
                (
                    f"rate_card.integration_surcharge_monthly_usd.{i}",
                    "fact.integrations_available",
                ),
            )
        )
    setup = dec(card.setup_fee_usd.per_module) * len(modules)
    lines.append(
        Line(
            "setup fee (one time)",
            float(setup),
            ("rate_card.setup_fee_usd.per_module", "request.module_ids"),
        )
    )
    return Estimate(
        status,
        modules,
        tuple(assumptions),
        card.version,
        card.currency,
        daily_volume=float(daily_volume),
        volume_factor=factor,
        integrations=distinct,
        lines=tuple(lines),
        monthly=_range(card, monthly),
        setup=_range(card, setup),
        notes=tuple(card.notes),
    )


# ---- from session facts to an estimate ----


def _by_id(facts: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(f["field_id"]): f for f in facts}


def _applies(f: Field, by_id: Mapping[str, Mapping[str, Any]]) -> bool:
    """A conditional field applies only when its condition holds (an unknown operand is false)."""
    if f.condition is None:
        return True
    operand = by_id.get(f.condition.field)
    return (
        operand is not None
        and operand.get("status") in ASSESSED
        and operand.get("value") == f.condition.equals
    )


def daily_volume_of(protocol: Protocol, fact: Mapping[str, Any]) -> float:
    """The fact's volume per business day. Weekly and monthly values convert with the protocol's constants."""
    value = fact.get("value")
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise EstimateInputError("daily_request_volume is not a positive number")
    period = fact.get("period") or "day"
    conv = protocol.conversions
    divisor = {
        "day": 1,
        "week": conv.business_days_per_week,
        "month": conv.business_days_per_month,
    }.get(period)
    if divisor is None:
        raise EstimateInputError(f"unknown volume period {period}")
    return float(dec(value) / dec(divisor))


def estimate_status(
    protocol: Protocol, facts: Sequence[Mapping[str, Any]]
) -> tuple[str, list[str]]:
    """D39 status and the assumption codes (`needs:`, `conflict:`, `not_reported:`, `estimated:`)."""
    by_id = _by_id(facts)

    def status_of(field_id: str) -> str:
        return str(by_id.get(field_id, {}).get("status", "missing"))

    conflicting = [f.id for f in protocol.fields if status_of(f.id) == "conflicting"]
    blocking = [f.id for f in protocol.fields if f.blocking and f.id in conflicting]
    if blocking:
        return "blocked", [f"conflict:{i}" for i in blocking]
    missing = [
        f.id
        for f in protocol.fields
        if "assessment" in f.required_for
        and _applies(f, by_id)
        and status_of(f.id) not in ASSESSED
    ]
    if missing:
        return "unavailable", [
            f"conflict:{i}" if i in conflicting else f"needs:{i}" for i in missing
        ]
    assumptions: list[str] = []
    for f in protocol.fields:
        if "quote" not in f.required_for or not _applies(f, by_id):
            continue
        st = status_of(f.id)
        if st == "estimated":
            assumptions.append(f"estimated:{f.id}")
        elif st not in STATED:
            assumptions.append(
                f"conflict:{f.id}" if st == "conflicting" else f"not_reported:{f.id}"
            )
    assumptions += [
        f"conflict:{i}" for i in conflicting if f"conflict:{i}" not in assumptions
    ]
    return ("scenario" if assumptions else "indicative"), assumptions


def plan_estimate(
    card: RateCard,
    protocol: Protocol,
    facts: Sequence[Mapping[str, Any]],
    model_args: Mapping[str, Any],
) -> Estimate:
    """The estimate for `estimate_cost`. `model_args` may hold `module_ids` only; everything else is read here."""
    modules = check_modules(card, parse_model_args(model_args))
    status, assumptions = estimate_status(protocol, facts)
    if status in ("blocked", "unavailable"):
        return Estimate(
            status, modules, tuple(assumptions), card.version, card.currency
        )
    by_id = _by_id(facts)
    volume = daily_volume_of(protocol, by_id["daily_request_volume"])
    raw = by_id.get("integrations_available", {}).get("value")
    integrations = (
        [i for i in raw if isinstance(i, str)] if isinstance(raw, list) else []
    )
    if not integrations:
        integrations = [DEFAULT_INTEGRATION]
        assumptions.append(f"integration_assumed:{DEFAULT_INTEGRATION}")
        status = "scenario"
    return price(
        card,
        modules,
        daily_volume=volume,
        integrations=integrations,
        status=status,
        assumptions=assumptions,
    )
