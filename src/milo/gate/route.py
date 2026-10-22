"""Routing of the two-stage input gate (SPEC D103 to D117, D147; protocol `evals/protocols/m5g-gate.md`).

    stage 1 greeting  -> fixed greeting reply, no model call, cost 0
    stage 1 refuse    -> fixed scope reply, no model call, cost 0
    stage 1 pass      -> the chain
    stage 1 undecided -> Jev `noul` P(in diagnosis):
        P <= T_REFUSE -> fixed scope reply, no chain call (cost: one gate call, D109 as amended)
        P >= T_PASS   -> the chain
        otherwise     -> the chain with the D108 reply check on (`band`)
        gate failure  -> the chain with the reply check on (`gate_failed`, never a refusal, D117)

The thresholds are observed dev probabilities written by the dev probe run to `evals/results/m5g/gate_dev.json`
(the order-statistic rule of the protocol, `compute_thresholds` below). Before that file exists, stage 2 never
refuses: every undecided message goes to the band. The discovery chain runs the D108 reply check on every reply,
so `band` is a recorded flag, not a second check.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from milo.gate.jev import (
    RUBRIC_VERSION,
    SCOPE_SUMMARY,
    JevFailure,
    JevGate,
    build_request,
    build_state,
    state_hash,
)
from milo.gate.stage1 import ROOT, Stage1, Stage1Outcome, default_stage1

GATE_DEV_PATH = ROOT / "evals" / "results" / "m5g" / "gate_dev.json"

Route = Literal["greeting", "refuse", "pass", "band"]

SCOPE_REPLY = (
    "I can only help with the diagnosis of your business: how requests, bookings and jobs come in and who "
    "handles them, the tools you use, your volumes, times and costs, what you would like to automate, and "
    "MILO's modules, pricing and report. Could you tell me about one of those?"
)
GREETING_REPLY = (
    "Hello, I am MILO. I help you describe how your business works today so we can see what could be "
    "automated and what it would cost."
)


@dataclass(frozen=True)
class Thresholds:
    """`t_refuse` and `t_pass` with the dev `sid`s that produced them. `t_refuse_sid is None` means stage 2
    refuses nothing (no out-of-scope dev probability lies below every in-scope one, or no dev run yet)."""

    t_refuse: float
    t_pass: float
    t_refuse_sid: str | None
    t_pass_sid: str | None
    min_in: float | None = None
    min_in_sid: str | None = None
    max_out: float | None = None
    max_out_sid: str | None = None
    rubric_version: str | None = None
    source: str = "none"

    @property
    def refuses(self) -> bool:
        return self.t_refuse_sid is not None

    @property
    def band_width(self) -> float:
        return round(self.t_pass - self.t_refuse, 12)

    def as_dict(self) -> dict[str, Any]:
        return {
            "t_refuse": self.t_refuse,
            "t_refuse_sid": self.t_refuse_sid,
            "t_pass": self.t_pass,
            "t_pass_sid": self.t_pass_sid,
            "min_in": self.min_in,
            "min_in_sid": self.min_in_sid,
            "max_out": self.max_out,
            "max_out_sid": self.max_out_sid,
            "band_width": self.band_width,
            "stage2_refuses": self.refuses,
            "rubric_version": self.rubric_version,
        }


# Before the dev probe run: stage 2 refuses nothing and passes nothing outright (everything is band).
NO_THRESHOLDS = Thresholds(t_refuse=0.0, t_pass=1.0, t_refuse_sid=None, t_pass_sid=None)


def compute_thresholds(
    rows: list[tuple[str, str, float]], rubric_version: str | None = RUBRIC_VERSION
) -> Thresholds:
    """The protocol's rule (D115, D147) over `(sid, label, P)` of undecided dev rows with a probability.

    `T_refuse` = the largest out-of-scope P strictly below `min_in` (none: 0, stage 2 refuses nothing).
    `T_pass` = the smallest in-scope P strictly above `max_out` (none: 1.0). Ties pick the first sid in sort order.
    """
    ins = sorted((p, sid) for sid, label, p in rows if label == "in_scope")
    outs = sorted((p, sid) for sid, label, p in rows if label == "out_of_scope")
    if not ins or not outs:
        raise ValueError("thresholds need at least one in-scope and one out-of-scope P")
    min_in, min_in_sid = ins[0]
    max_out = outs[-1][0]
    max_out_sid = min(sid for p, sid in outs if p == max_out)
    below = [(p, sid) for p, sid in outs if p < min_in]
    if below:
        top = max(p for p, _ in below)
        t_refuse, t_refuse_sid = top, min(sid for p, sid in below if p == top)
    else:
        t_refuse, t_refuse_sid = 0.0, None
    above = [(p, sid) for p, sid in ins if p > max_out]
    if above:
        t_pass, t_pass_sid = above[0]
    else:
        t_pass, t_pass_sid = 1.0, None
    return Thresholds(
        t_refuse=t_refuse,
        t_pass=t_pass,
        t_refuse_sid=t_refuse_sid,
        t_pass_sid=t_pass_sid,
        min_in=min_in,
        min_in_sid=min_in_sid,
        max_out=max_out,
        max_out_sid=max_out_sid,
        rubric_version=rubric_version,
        source="computed",
    )


class StaleThresholds(ValueError):
    """`gate_dev.json` was probed with another rubric version than the code's `RUBRIC_VERSION`."""


def load_thresholds(path: Path = GATE_DEV_PATH, *, strict: bool = False) -> Thresholds:
    """Thresholds from the committed dev result; `NO_THRESHOLDS` when the file is absent (before the live run).

    A file whose `rubric_version` differs from `RUBRIC_VERSION` is refused: the product falls back to
    `NO_THRESHOLDS` (stage 2 never refuses), and `strict=True` (the final run) raises `StaleThresholds`.
    """
    if not path.exists():
        return NO_THRESHOLDS
    doc = json.loads(path.read_text(encoding="utf-8"))
    th = doc["thresholds"]
    if th.get("rubric_version") != RUBRIC_VERSION:
        if strict:
            raise StaleThresholds(
                f"{path.name} was probed with rubric {th.get('rubric_version')!r}, the code has {RUBRIC_VERSION!r}"
            )
        return NO_THRESHOLDS
    return Thresholds(
        t_refuse=float(th["t_refuse"]),
        t_pass=float(th["t_pass"]),
        t_refuse_sid=th.get("t_refuse_sid"),
        t_pass_sid=th.get("t_pass_sid"),
        min_in=th.get("min_in"),
        min_in_sid=th.get("min_in_sid"),
        max_out=th.get("max_out"),
        max_out_sid=th.get("max_out_sid"),
        rubric_version=th.get("rubric_version"),
        source=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
    )


THRESHOLDS = load_thresholds()
T_REFUSE = (
    THRESHOLDS.t_refuse
)  # refuse P <= T_REFUSE (only when THRESHOLDS.refuses): a wrong one refuses an owner
T_PASS = THRESHOLDS.t_pass  # pass P >= T_PASS without the band flag: a wrong one lets off-topic text to the chain


def route_p(p: float, th: Thresholds) -> Route:
    if th.refuses and p <= th.t_refuse:
        return "refuse"
    if th.t_pass_sid is not None and p >= th.t_pass:
        return "pass"
    return "band"


@dataclass
class GateDecision:
    route: Route
    stage: int  # 1 settled by stage 1, 2 by Jev (or its failure)
    stage1: Stage1Outcome
    stage1_reason: str
    p: float | None = None
    gate_failed: bool = False
    failure: str | None = None
    model_calls: int = 0  # gate calls (Jev); chat-model calls are counted by the chain
    cost_usd: float = 0.0
    latency_s: float = 0.0
    jev_model: str | None = None
    jev_request: dict[str, Any] | None = None
    raw: Any = None
    state_hash: str | None = None
    rubric_version: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return self.route == "refuse"

    @property
    def band(self) -> bool:
        return self.route == "band"

    def trace_detail(self) -> dict[str, Any]:
        """For traces: no owner text (D141)."""
        return {
            "route": self.route,
            "stage": self.stage,
            "stage1": self.stage1,
            "p": self.p,
            "band": self.band,
            "gate_failed": self.gate_failed,
            "gate_calls": self.model_calls,
            "cost_usd": self.cost_usd,
            "jev_model": self.jev_model,
            "rubric_version": self.rubric_version,
            "state_hash": self.state_hash,
        }


class Gate:
    """The two-stage gate. `jev=None` (no key, offline) makes stage 2 fail open for every undecided message."""

    def __init__(
        self,
        stage1: Stage1 | None = None,
        jev: JevGate | None = None,
        thresholds: Thresholds | None = None,
        *,
        scope_summary: str = SCOPE_SUMMARY,
    ) -> None:
        self.stage1 = stage1 or default_stage1()
        self.jev = jev
        self.thresholds = thresholds if thresholds is not None else THRESHOLDS
        self.scope_summary = scope_summary

    def decide(
        self,
        message: str,
        recent_turns: list[Any] | None = None,
        *,
        scope_summary: str | None = None,
    ) -> GateDecision:
        outcome, reason = self.stage1.classify(message)
        if outcome == "greeting":
            return GateDecision("greeting", 1, outcome, reason)
        if outcome == "refuse":
            return GateDecision("refuse", 1, outcome, reason)
        if outcome == "pass":
            return GateDecision("pass", 1, outcome, reason)
        summary = self.scope_summary if scope_summary is None else scope_summary
        if self.jev is None:
            state = build_state(message, recent_turns, summary)
            return GateDecision(
                "band",
                2,
                outcome,
                reason,
                gate_failed=True,
                failure="no stage-2 client",
                jev_request=build_request(state),
                state_hash=state_hash(state),
                rubric_version=RUBRIC_VERSION,
            )
        started = time.monotonic()
        try:
            res = self.jev.ask(message, recent_turns, summary)
        except JevFailure as fail:
            return GateDecision(
                "band",
                2,
                outcome,
                reason,
                gate_failed=True,
                failure=fail.reason,
                model_calls=1,
                cost_usd=float(fail.cost_usd or 0.0),
                latency_s=fail.latency_s or round(time.monotonic() - started, 4),
                jev_model=fail.model,
                jev_request=fail.request,
                raw=fail.raw,
                state_hash=fail.state_hash,
                rubric_version=RUBRIC_VERSION,
            )
        return GateDecision(
            route_p(res.p, self.thresholds),
            2,
            outcome,
            reason,
            p=res.p,
            model_calls=1,
            cost_usd=float(res.cost_usd or 0.0),
            latency_s=res.latency_s,
            jev_model=res.model,
            jev_request=res.request,
            raw=res.raw,
            state_hash=res.state_hash,
            rubric_version=res.rubric_version,
        )


def offline_gate() -> Gate:
    """Stage 1 only; stage 2 fails open (no client). The default of `build_discovery_app` (tests, kit, evals)."""
    return Gate(jev=None)


def build_gate(settings: Any, *, ledger: Any = None) -> Gate:
    """The product gate for `create_app` (wired by the integrator after M6).

    With an OpenRouter key in `settings` (`milo.settings.Settings`): the live two-stage gate, Jev through
    `llm/cloud.py`, each call checked against and recorded in the spend ledger (`SpendLedger(budget_usd=...)` by
    default, the D18 log). Without a key: stage 1 only, stage 2 fails open (D117).
    """
    key = getattr(settings, "openrouter_api_key", "") or ""
    if not key:
        return offline_gate()
    from milo.gate.jev import JEV_PRICE_IN_USD_PER_MTOK
    from milo.llm.cloud import OpenRouterDecisions

    if ledger is None:
        from milo.spend import SpendLedger

        ledger = SpendLedger(budget_usd=float(getattr(settings, "budget_usd", 0.0)))
    client = OpenRouterDecisions(
        key, ledger=ledger, price_in_usd_per_mtok=JEV_PRICE_IN_USD_PER_MTOK
    )
    return Gate(jev=JevGate(client))


def decision_dict(d: GateDecision) -> dict[str, Any]:
    """The gate-callable contract of `evals/security/run_live.py` (`--gate milo.gate:sr_gate`)."""
    return {
        "stage1": d.stage1 if d.stage1 != "greeting" else "pass",
        "route": d.route,
        "model_calls": d.model_calls,
        "refused": d.refused,
        "gate_failed": d.gate_failed,
        "p": d.p,
        "band": d.band,
        "jev_request": d.jev_request,
        "jev_model": d.jev_model,
        "rubric_version": d.rubric_version,
        "state_hash": d.state_hash,
        # The callable does not run the chain, so the D108 reply check is not observed here.
        "d108_reply_check_passed": None,
        "cost_usd": d.cost_usd,
        "latency_s": d.latency_s,
    }


_SR_GATE: list[Gate] = []


def sr_gate(
    message: str, *, recent_turns: list[Any], scope_summary: str
) -> dict[str, Any]:
    """`--gate milo.gate:sr_gate` for the SR live cases: the product gate with a live Jev client. Live only
    (`MILO_LIVE=1` and `OPENROUTER_API_KEY`); `recent_turns` and `scope_summary` are forwarded as the caller sends
    them (A-05b sends poisoned turns on purpose)."""
    import os

    if os.environ.get("MILO_LIVE") != "1":
        raise RuntimeError("sr_gate calls Jev and needs MILO_LIVE=1")
    if not _SR_GATE:
        from milo.gate.jev import jev_from_env

        jev = jev_from_env(dict(os.environ))
        if jev is None:
            raise RuntimeError("sr_gate needs OPENROUTER_API_KEY")
        _SR_GATE.append(Gate(jev=jev))
    return decision_dict(
        _SR_GATE[0].decide(message, recent_turns, scope_summary=scope_summary)
    )
