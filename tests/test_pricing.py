"""M3 pricing tests: rate card, tiers, surcharges, ranges, status gating, and the no-model-numbers rule."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from milo.pricing.estimator import (
    EstimateInputError,
    ModelSuppliedNumber,
    estimate_status,
    parse_model_args,
    plan_estimate,
    price,
)
from milo.pricing.rate_card import (
    DEFAULT_CARD_PATH,
    get_rate_card,
    known_integrations,
    known_modules,
    money,
    volume_factor,
)
from milo.pricing.schema import load_rate_card
from milo.protocol.schema import Protocol, load_protocol
from milo.tools.backend import DEFAULT_PROTOCOL_PATH

CARD = get_rate_card()
PROTOCOL: Protocol = load_protocol(DEFAULT_PROTOCOL_PATH)


def _value_for(field: Any) -> Any:
    if field.type == "string":
        return "text"
    if field.type in ("integer", "number"):
        return int(field.min) if field.min is not None else 5
    if field.type == "boolean":
        return True
    if field.type == "enum":
        return field.values[0]
    if field.type == "list":
        return ["item"]
    return [field.values[0]]


def facts(
    overrides: dict[str, dict[str, Any]] | None = None,
    *,
    status: str = "reported",
    drop: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Every protocol field present with a valid value, then overrides and drops."""
    rows = {
        f.id: {
            "field_id": f.id,
            "value": _value_for(f),
            "unit": f.unit,
            "period": None,
            "status": status,
        }
        for f in PROTOCOL.fields
    }
    rows["daily_request_volume"].update(value=35, period="day")
    rows["integrations_available"].update(value=["api-connector"])
    for fid, change in (overrides or {}).items():
        rows[fid].update(change)
    return [r for fid, r in rows.items() if fid not in drop]


# ---- the rate card ----


def test_rate_card_loads_and_matches_the_committed_constants() -> None:
    assert CARD.version == "rate-card-v1-2026-10"
    assert CARD.currency == "USD"
    assert CARD.base_monthly_usd["phone-intake"] == 690
    assert CARD.setup_fee_usd.per_module == 900
    assert CARD.integration_surcharge_monthly_usd["api-connector"] == 300
    assert CARD.max_modules_per_estimate == 6
    assert known_modules(CARD) == list(CARD.base_monthly_usd)
    assert "export-only-tool" in known_integrations(CARD)
    assert get_rate_card() is get_rate_card(DEFAULT_CARD_PATH)
    assert load_rate_card(DEFAULT_CARD_PATH) == CARD


@pytest.mark.parametrize(
    ("daily", "factor"),
    [(1, 1.0), (20, 1.0), (20.5, 1.25), (21, 1.25), (60, 1.25), (61, 1.5), (150, 1.5)],
)
def test_volume_tiers_are_inclusive_at_the_upper_bound(
    daily: float, factor: float
) -> None:
    assert volume_factor(CARD, daily) == factor


@pytest.mark.parametrize("daily", [150.01, 151, 10_000])
def test_volume_above_the_last_tier_has_no_factor(daily: float) -> None:
    assert volume_factor(CARD, daily) is None


@pytest.mark.parametrize("daily", [0, -3])
def test_non_positive_volume_is_refused(daily: float) -> None:
    with pytest.raises(ValueError):
        volume_factor(CARD, daily)


def test_money_rounds_half_up_to_cents() -> None:
    assert money(Decimal("0.125")) == 0.13
    assert money(Decimal("862.5")) == 862.5
    assert money(Decimal("1.004")) == 1.0


# ---- the arithmetic ----


def test_estimate_example_with_tier_surcharge_setup_and_range() -> None:
    est = price(
        CARD,
        ["phone-intake", "customer-status-updates"],
        daily_volume=35,
        integrations=["api-connector"],
        status="indicative",
    )
    # (690 + 240) x 1.25 = 1162.50, plus one api-connector surcharge of 300
    assert est.volume_factor == 1.25
    assert est.monthly is not None and est.setup is not None
    assert (est.monthly.low, est.monthly.base, est.monthly.high) == (
        1316.25,
        1462.5,
        1755.0,
    )
    # two modules, 900 each, once
    assert (est.setup.low, est.setup.base, est.setup.high) == (1620.0, 1800.0, 2160.0)
    amounts = {line.label: line.amount_usd for line in est.lines}
    assert amounts["phone-intake monthly"] == 862.5
    assert amounts["customer-status-updates monthly"] == 300.0
    assert amounts["api-connector surcharge monthly"] == 300.0
    assert amounts["setup fee (one time)"] == 1800.0
    assert est.rate_card_version == CARD.version


def test_lowest_tier_has_no_uplift_and_csv_export_costs_nothing() -> None:
    est = price(
        CARD,
        ["ticket-creation", "email-triage"],
        daily_volume=19,
        integrations=["csv-export"],
        status="indicative",
    )
    assert est.monthly is not None
    assert (est.monthly.low, est.monthly.base, est.monthly.high) == (
        612.0,
        680.0,
        816.0,
    )


def test_an_integration_surcharge_counts_once_not_once_per_module() -> None:
    est = price(
        CARD,
        ["phone-intake", "email-triage", "ticket-creation"],
        daily_volume=10,
        integrations=["api-connector", "api-connector"],
        status="indicative",
    )
    assert est.integrations == ("api-connector",)
    assert sum(1 for x in est.lines if "surcharge" in x.label) == 1
    assert est.monthly is not None and est.monthly.base == 690 + 390 + 290 + 300


def test_two_different_integrations_both_apply() -> None:
    est = price(
        CARD,
        ["document-data-entry"],
        daily_volume=10,
        integrations=["export-only-tool", "crm-connector"],
        status="indicative",
    )
    assert est.monthly is not None and est.monthly.base == 490 + 250 + 450


def test_setup_fee_is_per_module() -> None:
    one = price(
        CARD,
        ["phone-intake"],
        daily_volume=5,
        integrations=["csv-export"],
        status="indicative",
    )
    six = price(
        CARD,
        known_modules(CARD),
        daily_volume=5,
        integrations=["csv-export"],
        status="indicative",
    )
    assert one.setup is not None and six.setup is not None
    assert one.setup.base == 900 and six.setup.base == 5400


def test_volume_above_the_card_gives_no_figures() -> None:
    est = price(
        CARD,
        ["phone-intake"],
        daily_volume=151,
        integrations=["csv-export"],
        status="indicative",
    )
    assert est.status == "unavailable"
    assert est.monthly is None and est.setup is None and est.lines == ()
    assert "volume_above_rate_card" in est.assumptions


def test_every_line_names_its_sources() -> None:
    est = price(
        CARD,
        ["phone-intake"],
        daily_volume=35,
        integrations=["api-connector"],
        status="indicative",
    )
    assert est.lines
    for line in est.lines:
        assert line.sources
        assert all(
            s.startswith(("rate_card.", "fact.", "request.")) for s in line.sources
        )
    payload = est.event_payload()
    assert payload["monthly"]["base"] == est.monthly.base  # type: ignore[union-attr]
    assert payload["rate_card_version"] == CARD.version


@pytest.mark.parametrize(
    "modules",
    [
        [],
        ["phone-intake", "phone-intake"],
        ["no-such-module"],
        [*known_modules(CARD), "phone-intake"],
    ],
)
def test_module_lists_the_card_cannot_price_are_refused(modules: list[str]) -> None:
    with pytest.raises(EstimateInputError):
        price(
            CARD,
            modules,
            daily_volume=5,
            integrations=["csv-export"],
            status="indicative",
        )


def test_unknown_integration_is_refused() -> None:
    with pytest.raises(EstimateInputError):
        price(
            CARD,
            ["phone-intake"],
            daily_volume=5,
            integrations=["carrier-pigeon"],
            status="indicative",
        )


def test_a_status_without_figures_cannot_be_priced() -> None:
    for status in ("blocked", "unavailable"):
        with pytest.raises(EstimateInputError):
            price(
                CARD,
                ["phone-intake"],
                daily_volume=5,
                integrations=["csv-export"],
                status=status,
            )


# ---- the estimator takes no numbers from the model ----


@pytest.mark.parametrize(
    "extra",
    [
        {"daily_request_volume": 35},
        {"volume": 1},
        {"price": 0},
        {"total": 0.0},
        {"integrations": ["csv-export"]},
        {"user_id": "owner-a"},
    ],
)
def test_estimator_rejects_model_supplied_numbers(extra: dict[str, Any]) -> None:
    args = {"module_ids": ["phone-intake"], **extra}
    with pytest.raises(ModelSuppliedNumber):
        parse_model_args(args)
    with pytest.raises(ModelSuppliedNumber):
        plan_estimate(CARD, PROTOCOL, facts(), args)


@pytest.mark.parametrize("ids", [[], "phone-intake", None, [1, 2], ["phone-intake", 3]])
def test_module_ids_must_be_a_list_of_strings(ids: Any) -> None:
    with pytest.raises(EstimateInputError):
        parse_model_args({"module_ids": ids})


def test_the_figures_follow_the_facts_not_the_arguments() -> None:
    args = {"module_ids": ["phone-intake"]}
    low = plan_estimate(
        CARD, PROTOCOL, facts({"daily_request_volume": {"value": 10}}), args
    )
    high = plan_estimate(
        CARD, PROTOCOL, facts({"daily_request_volume": {"value": 100}}), args
    )
    assert low.volume_factor == 1.0 and high.volume_factor == 1.5
    assert low.monthly is not None and high.monthly is not None
    assert low.monthly.base == 690 + 300
    assert high.monthly.base == 690 * 1.5 + 300


def _numbers_in(value: Any) -> list[Any]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return [value]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _numbers_in(v)]
    if isinstance(value, list | tuple):
        return [n for v in value for n in _numbers_in(v)]
    return []


def test_the_tool_output_the_model_sees_holds_no_figure() -> None:
    est = plan_estimate(
        CARD, PROTOCOL, facts(), {"module_ids": ["phone-intake", "email-triage"]}
    )
    out = est.tool_output()
    assert set(out) == {"status", "module_ids", "assumptions"}
    assert _numbers_in(out) == []
    assert _numbers_in(est.event_payload()) != []


# ---- status gating (D39) ----


def test_empty_facts_give_unavailable_with_needs() -> None:
    status, assumptions = estimate_status(PROTOCOL, [])
    assert status == "unavailable"
    assert "needs:daily_request_volume" in assumptions
    est = plan_estimate(CARD, PROTOCOL, [], {"module_ids": ["phone-intake"]})
    assert est.status == "unavailable" and est.monthly is None


def test_all_quote_fields_reported_is_indicative() -> None:
    est = plan_estimate(CARD, PROTOCOL, facts(), {"module_ids": ["phone-intake"]})
    assert est.status == "indicative" and est.assumptions == ()
    assert est.monthly is not None


def test_a_missing_quote_field_makes_a_scenario_with_the_assumption() -> None:
    est = plan_estimate(
        CARD,
        PROTOCOL,
        facts(drop=("start_timeline",)),
        {"module_ids": ["phone-intake"]},
    )
    assert est.status == "scenario"
    assert "not_reported:start_timeline" in est.assumptions
    assert est.monthly is not None


def test_estimated_facts_make_a_scenario() -> None:
    est = plan_estimate(
        CARD, PROTOCOL, facts(status="estimated"), {"module_ids": ["phone-intake"]}
    )
    assert est.status == "scenario"
    assert "estimated:daily_request_volume" in est.assumptions


def test_a_conflicting_volume_blocks_the_estimate() -> None:
    est = plan_estimate(
        CARD,
        PROTOCOL,
        facts({"daily_request_volume": {"status": "conflicting"}}),
        {"module_ids": ["phone-intake"]},
    )
    assert est.status == "blocked"
    assert est.monthly is None and est.setup is None and est.lines == ()
    assert est.assumptions == ("conflict:daily_request_volume",)


def test_a_conflict_in_a_non_blocking_field_is_not_an_indicative_estimate() -> None:
    est = plan_estimate(
        CARD,
        PROTOCOL,
        facts({"budget_range": {"status": "conflicting"}}),
        {"module_ids": ["phone-intake"]},
    )
    assert est.status == "scenario"
    assert "conflict:budget_range" in est.assumptions


def test_no_reported_integration_assumes_csv_export_as_a_scenario() -> None:
    est = plan_estimate(
        CARD,
        PROTOCOL,
        facts({"integrations_available": {"value": [], "status": "estimated"}}),
        {"module_ids": ["phone-intake"]},
    )
    assert est.status == "scenario"
    assert "integration_assumed:csv-export" in est.assumptions
    assert est.integrations == ("csv-export",)


@pytest.mark.parametrize(
    ("value", "period", "factor"),
    [
        (420, "month", 1.0),
        (441, "month", 1.25),
        (100, "week", 1.0),
        (105, "week", 1.25),
    ],
)
def test_monthly_and_weekly_volumes_convert_with_protocol_constants(
    value: int, period: str, factor: float
) -> None:
    est = plan_estimate(
        CARD,
        PROTOCOL,
        facts({"daily_request_volume": {"value": value, "period": period}}),
        {"module_ids": ["phone-intake"]},
    )
    assert est.volume_factor == factor


def test_a_volume_above_the_card_through_the_facts_is_unavailable() -> None:
    est = plan_estimate(
        CARD,
        PROTOCOL,
        facts({"daily_request_volume": {"value": 200}}),
        {"module_ids": ["phone-intake"]},
    )
    assert est.status == "unavailable" and est.monthly is None
