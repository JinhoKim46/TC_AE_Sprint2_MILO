"""The canonical report JSON, built from a stored specification snapshot (SPEC sections 0, 7, 17 M5c; D40, D79).

No model call and no randomness: the same snapshot and rate card always give the same JSON. Rules this module
enforces in code:

- Every number is a figure `{"value", "source": [...], ...}`. A source is `fact.<field_id>`,
  `rate_card.<key path>`, `protocol.conversions.<key>` or `derived.<name>`; `resolve_source` checks one.
  Nothing else in the JSON is a number (integrator decision 66: the report shows only code-computed figures).
- When the protocol does not allow a figure (state `collecting`, or a blocking conflict, or an unavailable
  estimate) the report holds no number at all and says so in `numbers`.
- A conflicting fact is an open question with the protocol's conflict question; its values are not shown.
- Owner text (free-text facts and their evidence) appears only as `{"quoted": ..., "source": ...}`. M6 escapes
  it (D139). The owner's e-mail address (pii) never enters the report.
- The range factors apply to the monthly total only; the setup fee is one fixed figure (decision 31).
- A rate-card key the report needs but the card lacks raises `MissingRateCardKey`; nothing is invented.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from milo.pricing.estimator import (
    DEFAULT_INTEGRATION,
    NUMBER_STATUSES,
    EstimateInputError,
    daily_volume_of,
    plan_estimate,
)
from milo.pricing.rate_card import dec, get_rate_card, money
from milo.pricing.schema import RateCard
from milo.protocol.engine import ProtocolEngine
from milo.protocol.loader import get_protocol
from milo.protocol.schema import Field, Protocol

REPORT_VERSION = "report.v1"
ASSESSED = ("reported", "confirmed", "estimated")
FOOTER = (
    "This report is indicative and non-binding. Figures come from the rate card and from what the owner "
    "reported; an operator reviews every submission."
)

# Which words in which facts point at which catalogue module. Rule text only; no number lives here.
MODULE_SIGNALS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "phone-intake": (
        ("request_channels", ("phone", "emergency line", "call")),
        ("automation_goals", ("intake", "calls", "phone")),
        ("pain_points", ("missed call", "night call")),
    ),
    "email-triage": (
        ("request_channels", ("inbox", "email", "e-mail", "mail")),
        ("automation_goals", ("triage", "inbox", "email")),
        ("pain_points", ("lost in the inbox", "inbox")),
    ),
    "ticket-creation": (
        ("automation_goals", ("ticket", "job sheet", "booking intake")),
        ("steps_in_process", ("job sheet",)),
    ),
    "staff-assignment": (
        ("automation_goals", ("assign", "dispatch", "routing", "schedul")),
        ("steps_in_process", ("dispatch",)),
    ),
    "document-data-entry": (
        ("automation_goals", ("re-key", "rekey", "data entry", "typing", "paperwork")),
        ("pain_points", ("re-key", "rekey", "data entry")),
    ),
    "customer-status-updates": (
        ("automation_goals", ("reminder", "status", "update", "confirm")),
        ("pain_points", ("reminder", "no-show")),
    ),
}

ASSUMPTION_TEXT = {
    "needs": "A required answer is still missing: {f}.",
    "conflict": "The owner gave conflicting answers for {f}; this must be confirmed.",
    "not_reported": "{f} was not stated by the owner; the figure assumes the common case.",
    "estimated": "{f} is an estimate, not a stated value.",
    "integration_assumed": "No integration was reported; {f} is assumed and carries no surcharge.",
    "volume_above_rate_card": "The volume is above the last rate-card tier; a person prices it.",
}


class ReportError(ValueError):
    """Base of the report builder's typed errors."""


class MissingRateCardKey(ReportError):
    """The rate card lacks a key the report needs. The report never invents a constant."""

    def __init__(self, key: str) -> None:
        super().__init__(f"rate card has no key {key}")
        self.key = key


class SnapshotError(ReportError):
    """The stored snapshot cannot be read as a specification."""


# ---- small helpers ----


def fig(value: float | int, *sources: str, **extra: Any) -> dict[str, Any]:
    return {"value": value, "source": list(sources), **extra}


def quoted(text: Any, source: str) -> dict[str, Any]:
    return {"quoted": str(text), "source": source}


def _rc(card: RateCard, table: str, key: str | None = None) -> Any:
    """A rate-card constant, or `MissingRateCardKey`."""
    t = getattr(card, table, None)
    if t is None:
        raise MissingRateCardKey(f"rate_card.{table}")
    if key is None:
        return t
    try:
        return t[key] if isinstance(t, dict) else getattr(t, key)
    except (KeyError, AttributeError):
        raise MissingRateCardKey(f"rate_card.{table}.{key}") from None


def make_snapshot(
    protocol: Protocol, facts: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The `spec_snapshot_json` shape: `{protocol_version, facts: [{field_id, value, unit, period, status,
    evidence_text, values?}]}` in protocol order."""
    by_id = {str(f["field_id"]): dict(f) for f in facts}
    return {
        "protocol_version": protocol.version,
        "facts": [by_id[f.id] for f in protocol.fields if f.id in by_id],
    }


def _facts_of(
    snapshot: Mapping[str, Any], protocol: Protocol
) -> dict[str, dict[str, Any]]:
    raw = snapshot.get("facts") if isinstance(snapshot, Mapping) else None
    if not isinstance(raw, list):
        raise SnapshotError("snapshot has no facts list")
    known = {f.id for f in protocol.fields}
    out: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping) or "field_id" not in item:
            raise SnapshotError("a fact has no field_id")
        fid = str(item["field_id"])
        if fid not in known:
            raise SnapshotError(f"unknown field {fid}")
        out[fid] = dict(item)
    return out


def resolve_source(
    source: str, card: RateCard, protocol: Protocol, fact_ids: Sequence[str]
) -> Any:
    """The value a source pointer names. Raises `MissingRateCardKey` or `ReportError` for a dangling pointer."""
    kind, _, rest = source.partition(".")
    if kind == "fact":
        if rest not in fact_ids:
            raise ReportError(f"source {source} names no fact")
        return rest
    if kind == "derived":
        return None
    if kind == "protocol":
        node: Any = protocol.model_dump()
        for part in rest.split("."):
            if not isinstance(node, dict) or part not in node:
                raise ReportError(f"source {source} names nothing in the protocol")
            node = node[part]
        return node
    if kind == "rate_card":
        node = card.model_dump()
        for part in rest.split("."):
            if not isinstance(node, dict) or part not in node:
                raise MissingRateCardKey(source)
            node = node[part]
        return node
    raise ReportError(f"unknown source kind {source}")


# ---- facts as report items ----


def _is_text(f: Field) -> bool:
    return f.type in ("string", "list")


def _item(f: Field, fact: Mapping[str, Any], *, numbers: bool) -> dict[str, Any] | None:
    """A fact as `{field, status, value|quoted, source}`; None when it would carry a withheld number."""
    value = fact.get("value")
    src = f"fact.{f.id}"
    out: dict[str, Any] = {"field": f.id, "status": fact.get("status"), "source": [src]}
    if f.type in ("integer", "number"):
        if not numbers or isinstance(value, bool) or not isinstance(value, int | float):
            return None
        out["figure"] = fig(
            value, src, unit=fact.get("unit"), period=fact.get("period")
        )
    elif _is_text(f):
        if isinstance(value, list):
            out["quoted_items"] = [quoted(v, src) for v in value if isinstance(v, str)]
        elif isinstance(value, str):
            out["quoted"] = value
        else:
            return None
    else:
        out["value"] = value
    return out


def _usable(facts: Mapping[str, Mapping[str, Any]], fid: str) -> bool:
    return facts.get(fid, {}).get("status") in ASSESSED


def _texts(facts: Mapping[str, Mapping[str, Any]], fid: str) -> list[str]:
    fact = facts.get(fid)
    if fact is None or fact.get("status") not in ASSESSED:
        return []
    v = fact.get("value")
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return []


def _evidence(
    facts: Mapping[str, Mapping[str, Any]], fid: str
) -> dict[str, Any] | None:
    text = facts.get(fid, {}).get("evidence_text")
    return quoted(text, f"fact.{fid}") if isinstance(text, str) and text else None


# ---- candidates and modules ----


def _matches(
    facts: Mapping[str, Mapping[str, Any]], module: str
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for fid, words in MODULE_SIGNALS[module]:
        for text in _texts(facts, fid):
            low = text.lower()
            hit = next((w for w in words if w in low), None)
            if hit is not None:
                found.append(
                    {
                        "field": fid,
                        "keyword": hit,
                        "quoted": text,
                        "source": f"fact.{fid}",
                    }
                )
    return found


def _saved_minutes(
    card: RateCard, facts: Mapping[str, Mapping[str, Any]], module: str
) -> tuple[Decimal, list[str], dict[str, Any]]:
    """Minutes saved per automated request: the rate-card constant, capped at the reported manual time.

    The rate card defines `automated_minutes_per_request` as the minutes one automated request saves
    (docs/m5c_report.md, open point 1)."""
    rate = dec(_rc(card, "automated_minutes_per_request", module))
    handling = facts.get("average_handling_minutes", {})
    entry = facts.get("manual_entry_minutes", {})
    t_manual = Decimal(0)
    srcs = [f"rate_card.automated_minutes_per_request.{module}"]
    inputs: dict[str, Any] = {}
    for fid, fact in (
        ("average_handling_minutes", handling),
        ("manual_entry_minutes", entry),
    ):
        v = fact.get("value")
        if (
            fact.get("status") in ASSESSED
            and isinstance(v, int | float)
            and not isinstance(v, bool)
        ):
            t_manual += dec(v)
            srcs.append(f"fact.{fid}")
            inputs[fid] = {
                "label": "confirmed"
                if fact["status"] == "confirmed"
                else ("reported" if fact["status"] == "reported" else "assumed"),
                "figure": fig(v, f"fact.{fid}"),
            }
    saved = min(rate, t_manual) if t_manual > 0 else rate
    return saved, srcs, inputs


def _assumption_items(codes: Sequence[str]) -> list[dict[str, Any]]:
    out = []
    for code in codes:
        kind, _, target = code.partition(":")
        tmpl = ASSUMPTION_TEXT.get(kind, "{f}")
        out.append({"code": code, "text": tmpl.format(f=target)})
    return out


# ---- the report ----


def build_report(
    snapshot: Mapping[str, Any],
    *,
    card: RateCard | None = None,
    protocol: Protocol | None = None,
) -> dict[str, Any]:
    card = card or get_rate_card()
    protocol = protocol or get_protocol()
    facts = _facts_of(snapshot, protocol)
    engine = ProtocolEngine(protocol)
    view = engine.evaluate(facts)
    fact_list = [facts[f.id] for f in protocol.fields if f.id in facts]

    # modules from the facts: code only, no model
    candidates: list[dict[str, Any]] = []
    for module in MODULE_SIGNALS:
        hits = _matches(facts, module)
        if hits:
            _rc(card, "base_monthly_usd", module)  # typed error if the card lacks it
            candidates.append({"module": module, "matches": hits})
    # rank by matches, then card order; the cap is the card's
    max_modules = int(_rc(card, "max_modules_per_estimate"))

    numbers_ok = view.number_allowed
    estimate = None
    reason: str | None = None
    if not numbers_ok:
        reason = f"protocol_state:{view.state}"
    elif not candidates:
        numbers_ok, reason = False, "no_module_matched"
    if numbers_ok:
        ranked = _rank(card, facts, protocol, candidates)
        chosen = [c["module"] for c in ranked[:max_modules]]
        for i in _integrations(facts) or [DEFAULT_INTEGRATION]:
            _rc(card, "integration_surcharge_monthly_usd", i)
        _rc(card, "setup_fee_usd", "per_module")
        _rc(card, "range")
        try:
            estimate = plan_estimate(
                card, protocol, [dict(f) for f in fact_list], {"module_ids": chosen}
            )
        except (
            EstimateInputError
        ) as exc:  # pragma: no cover - the pre-checks above name every key
            raise ReportError(str(exc)) from exc
        if estimate.status not in NUMBER_STATUSES:
            numbers_ok, reason, estimate = False, f"estimate:{estimate.status}", None
        else:
            candidates = ranked
            for c in candidates:
                c["recommended"] = c["module"] in chosen
    if not numbers_ok:
        for c in candidates:
            c.pop("score", None)
            c["recommended"] = False

    report: dict[str, Any] = {
        "version": REPORT_VERSION,
        "protocol_version": protocol.version,
        "rate_card_version": card.version,
        "protocol_state": view.state,
        "numbers": {"allowed": numbers_ok, "reason": reason},
        "sections": [],
    }
    sections: list[dict[str, Any]] = report["sections"]

    def add(sid: str, title: str, **body: Any) -> None:
        sections.append({"id": sid, "title": title, **body})

    conflicts = [
        {
            "field": c,
            "question": engine.question_text(f"confirm_{c}"),
            "evidence": _evidence(facts, c),
            "source": [f"fact.{c}"],
        }
        for c in view.conflicts
    ]

    # 1 summary: structured, no owner text in a sentence
    add(
        "summary",
        "Summary",
        estimate_status=None if estimate is None else estimate.status,
        protocol_state=view.state,
        company=_evidence_value(facts, "company_name"),
        module_ids=[c["module"] for c in candidates if c.get("recommended")],
    )
    # 2 profile
    profile = []
    for fid in (
        "company_name",
        "industry",
        "owner_name",
        "owner_role",
        "employee_count",
        "locations",
    ):
        if fid in facts and facts[fid].get("status") in ASSESSED:
            it = _item(protocol.field_by_id(fid), facts[fid], numbers=numbers_ok)
            if it is not None:
                profile.append(it)
    add("company_profile", "Company profile", items=profile)
    # 3 pipeline
    pipeline = []
    for fid in (
        "request_channels",
        "daily_request_volume",
        "average_handling_minutes",
        "manual_entry_minutes",
        "tools_in_use",
        "steps_in_process",
        "approval_steps",
    ):
        if fid in facts and facts[fid].get("status") in ASSESSED:
            it = _item(protocol.field_by_id(fid), facts[fid], numbers=numbers_ok)
            if it is not None:
                pipeline.append(it)
    add("current_pipeline", "Current pipeline", items=pipeline)
    # 4 diagnosis
    diag = []
    for fid in (
        "pain_points",
        "error_types",
        "automation_goals",
        "main_goal",
        "urgent_requests_exist",
        "personal_data_handled",
    ):
        if fid in facts and facts[fid].get("status") in ASSESSED:
            it = _item(protocol.field_by_id(fid), facts[fid], numbers=numbers_ok)
            if it is not None:
                ev = _evidence(facts, fid)
                if ev is not None:
                    it["evidence"] = ev
                diag.append(it)
    add("diagnosis", "Diagnosis", items=diag)
    # 5 candidates
    cand_out = []
    for c in candidates:
        row: dict[str, Any] = {
            "module": c["module"],
            "matches": c["matches"],
            "recommended": c.get("recommended", False),
            "source": [f"rate_card.base_monthly_usd.{c['module']}"],
        }
        if "score" in c:
            row["score"] = c["score"]
        cand_out.append(row)
    add("automation_candidates", "Automation candidates", candidates=cand_out)
    # 6 recommended
    add(
        "recommended_modules",
        "Recommended modules",
        modules=[
            {
                "module": c["module"],
                "source": [f"rate_card.base_monthly_usd.{c['module']}"],
            }
            for c in candidates
            if c.get("recommended")
        ],
    )
    # 7 cost, 8 return
    add(
        "cost_estimate",
        "Cost estimate",
        **_cost_section(card, estimate, view.state, reason),
    )
    add(
        "return_estimate",
        "Simple return estimate",
        **_return_section(card, protocol, facts, candidates, estimate, reason),
    )
    # 9 unknowns and next steps
    unknown = [
        {"field": f.id, "source": [f"fact.{f.id}"]}
        for f in protocol.fields
        if facts.get(f.id, {}).get("status") == "unknown"
    ]
    steps = (
        [
            {
                "code": "answer_open_questions",
                "text": "Answer the open questions so the figures can be confirmed.",
            }
        ]
        if conflicts
        else []
    )
    steps += [
        {
            "code": "operator_review",
            "text": "An operator reviews the submission and contacts you.",
        }
    ]
    if view.missing:
        steps.append(
            {
                "code": "complete_discovery",
                "text": "Finish the discovery interview so a figure can be shown.",
            }
        )
    add(
        "unknowns_and_next_steps",
        "Unknowns and next steps",
        open_questions=conflicts,
        unknown_fields=unknown,
        missing_fields=[
            {"field": m, "question": engine.question_text(m)} for m in view.missing
        ],
        assumptions=[] if estimate is None else _assumption_items(estimate.assumptions),
        next_steps=steps,
    )
    # 10 evidence appendix
    evidence = []
    for f in protocol.fields:
        fact = facts.get(f.id)
        if fact is None or f.pii:
            continue
        ev = _evidence(facts, f.id)
        evidence.append(
            {
                "field": f.id,
                "status": fact.get("status"),
                "evidence": ev,
                "source": [f"fact.{f.id}"],
            }
        )
    add("evidence_appendix", "Evidence appendix", evidence=evidence, footer=FOOTER)
    return report


def _evidence_value(
    facts: Mapping[str, Mapping[str, Any]], fid: str
) -> dict[str, Any] | None:
    v = facts.get(fid, {}).get("value")
    return (
        quoted(v, f"fact.{fid}") if isinstance(v, str) and _usable(facts, fid) else None
    )


def _integrations(facts: Mapping[str, Mapping[str, Any]]) -> list[str]:
    raw = facts.get("integrations_available", {}).get("value")
    return [i for i in raw if isinstance(i, str)] if isinstance(raw, list) else []


def _daily(protocol: Protocol, facts: Mapping[str, Mapping[str, Any]]) -> Decimal:
    return dec(daily_volume_of(protocol, facts["daily_request_volume"]))


def _rank(
    card: RateCard,
    facts: Mapping[str, Mapping[str, Any]],
    protocol: Protocol,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Score = minutes saved per business day = volume x saved minutes x eligible share (all code)."""
    daily = _daily(protocol, facts)
    for c in candidates:
        m = c["module"]
        saved, srcs, _ = _saved_minutes(card, facts, m)
        share = dec(_rc(card, "eligible_share", m))
        c["score"] = fig(
            money(daily * saved * share),
            *srcs,
            f"rate_card.eligible_share.{m}",
            "fact.daily_request_volume",
            unit="minutes_per_business_day",
        )
    order = {m: n for n, m in enumerate(MODULE_SIGNALS)}
    return sorted(candidates, key=lambda c: (-c["score"]["value"], order[c["module"]]))


def _cost_section(
    card: RateCard, est: Any, state: str, reason: str | None
) -> dict[str, Any]:
    if est is None:
        return {
            "status": "none" if state == "collecting" else (reason or "unavailable"),
            "available": False,
        }
    monthly_src = ["derived.monthly_total", "rate_card.range"]
    lines = [
        {
            "label": x.label,
            "amount": fig(
                x.amount_usd,
                *[
                    s if not s.startswith("request.") else "derived.recommended_modules"
                    for s in x.sources
                ],
            ),
        }
        for x in est.lines
        if x.label != "setup fee (one time)"
    ]
    setup_line = next(x for x in est.lines if x.label == "setup fee (one time)")
    return {
        "available": True,
        "status": est.status,
        "currency": est.currency,
        "module_ids": list(est.module_ids),
        "lines": lines,
        "monthly_total": {
            "low": fig(est.monthly.low, *monthly_src, "rate_card.range.low_factor"),
            "base": fig(est.monthly.base, *monthly_src, "rate_card.range.base_factor"),
            "high": fig(est.monthly.high, *monthly_src, "rate_card.range.high_factor"),
        },
        "setup_fee": fig(
            setup_line.amount_usd,
            "rate_card.setup_fee_usd.per_module",
            "derived.recommended_modules",
            fixed=True,
        ),
        "range_applies_to": "monthly_total_only",
        "assumptions": _assumption_items(est.assumptions),
        "rate_card_version": est.rate_card_version,
        "notes": list(est.notes),
    }


def _return_section(
    card: RateCard,
    protocol: Protocol,
    facts: Mapping[str, Mapping[str, Any]],
    candidates: list[dict[str, Any]],
    est: Any,
    reason: str | None,
) -> dict[str, Any]:
    if est is None:
        return {"available": False, "status": "unavailable"}
    if card.automated_minutes_per_request is None or card.eligible_share is None:
        return {
            "available": False,
            "status": "unavailable",
            "reason": "rate_card_inputs_missing",
        }
    daily = _daily(protocol, facts)
    days = dec(protocol.conversions.business_days_per_month)
    rows = []
    total = Decimal(0)
    for c in candidates:
        if not c.get("recommended"):
            continue
        m = c["module"]
        saved, srcs, inputs = _saved_minutes(card, facts, m)
        share = dec(_rc(card, "eligible_share", m))
        hours = money(daily * saved * share / 60 * days)
        total += dec(hours)
        rows.append(
            {
                "module": m,
                "inputs": {
                    **inputs,
                    "saved_minutes": {
                        "label": "assumed",
                        "figure": fig(float(saved), *srcs),
                    },
                    "eligible_share": {
                        "label": "assumed",
                        "figure": fig(float(share), f"rate_card.eligible_share.{m}"),
                    },
                },
                "hours_per_month": fig(
                    hours,
                    "fact.daily_request_volume",
                    *srcs,
                    f"rate_card.eligible_share.{m}",
                    "protocol.conversions.business_days_per_month",
                    unit="hours",
                ),
            }
        )
    return {
        "available": True,
        "status": est.status,
        "formula": "daily volume x minutes saved x eligible share / 60 x business days per month",
        "modules": rows,
        "total_hours_per_month": fig(
            money(total),
            *[f"derived.hours.{r['module']}" for r in rows],
            unit="hours",
            note_code="modules_may_overlap",
        ),
    }


def attach_fields(report: Mapping[str, Any]) -> dict[str, Any]:
    """The columns `attach_report` stores next to the JSON: status, monthly low, base and high, currency, card."""
    cost = next(s for s in report["sections"] if s["id"] == "cost_estimate")
    if not cost.get("available"):
        return {
            "estimate_status": cost.get("status"),
            "rate_card_version": report["rate_card_version"],
        }
    m = cost["monthly_total"]
    return {
        "estimate_status": cost["status"],
        "cost_low": m["low"]["value"],
        "cost_base": m["base"]["value"],
        "cost_high": m["high"]["value"],
        "currency": cost["currency"],
        "rate_card_version": cost["rate_card_version"],
    }
