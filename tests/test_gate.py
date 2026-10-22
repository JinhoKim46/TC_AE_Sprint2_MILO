"""M5G input gate (SPEC D103 to D118, D145, D147, D157, D158; protocol evals/protocols/m5g-gate.md; HARNESS rule 11).

Offline: sockets are blocked, stage 2 runs on recorded or scripted Decisions responses, and the scope set is read
for `split == dev` only (the stage-1 comparison with the reference simulation also covers every probe).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from fakes import ChatReply, FakeBackend, HashEmbedder, ScriptedChat, Usage

from milo.app import build_app, build_discovery_app
from milo.gate import route as route_mod
from milo.gate.jev import (
    CRITERIA,
    INSTRUCTIONS,
    JEV_MODEL,
    QUESTION_KEY,
    SCOPE_SUMMARY,
    JevGate,
    build_request,
    build_state,
    parse_response,
    state_hash,
)
from milo.gate.route import (
    GREETING_REPLY,
    NO_THRESHOLDS,
    SCOPE_REPLY,
    Gate,
    Thresholds,
    compute_thresholds,
    decision_dict,
    load_thresholds,
    route_p,
    sr_gate,
)
from milo.gate.stage1 import Stage1, default_stage1, load_lexicon
from milo.llm.cloud import OPENROUTER_DECISIONS_URL, OpenRouterDecisions

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / "data" / "scope"
FIXTURES = ROOT / "evals" / "gate" / "fixtures"
DRY_REPLAY = ROOT / "evals" / "results" / "m5g" / "jev_replay_dry.jsonl"


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(x)
        for x in path.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]


DEV = [r for r in _jsonl(SCOPE / "scope_set.jsonl") if r["split"] == "dev"]
PROBES = [p for p in _jsonl(SCOPE / "probes.jsonl") if p["category"] != "empty"]


def _sim() -> Any:
    spec = importlib.util.spec_from_file_location(
        "stage1_sim", SCOPE / "tools" / "stage1_sim.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # no __pycache__ under data/ (MANIFEST.json covers every file there)
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = keep
    return mod.Stage1(mod.load_lexicon())


# ---------------------------------------------------------------- stage 1


def test_stage1_reproduces_the_reference_simulation_on_dev_rows_and_probes():
    sim, gate = _sim(), default_stage1()
    assert gate.version == "scope-lexicon.v4"
    rows = DEV + PROBES
    assert len(DEV) == 210 and len(PROBES) == 78
    diffs = [
        r["sid"] for r in rows if sim.classify(r["text"]) != gate.classify(r["text"])
    ]
    assert diffs == []


def test_stage1_refuses_no_in_scope_dev_row_and_settles_dev_like_the_audit():
    gate = default_stage1()
    counts: dict[tuple[str, str], int] = {}
    for r in DEV:
        out, _ = gate.classify(r["text"])
        counts[(r["label"], out)] = counts.get((r["label"], out), 0) + 1
    assert counts.get(("in_scope", "refuse"), 0) == 0
    # data/scope/README.md table (lexicon v4)
    assert counts == {
        ("in_scope", "greeting"): 2,
        ("in_scope", "pass"): 84,
        ("in_scope", "undecided"): 56,
        ("out_of_scope", "refuse"): 14,
        ("out_of_scope", "undecided"): 54,
    }


def test_probe_stage1_outcomes_are_recorded_per_row():
    gate = default_stage1()
    outcomes = {p["sid"]: gate.classify(p["text"])[0] for p in PROBES}
    # no probe that expects a pass is refused by stage 1
    assert not [
        p["sid"]
        for p in PROBES
        if p["expected"] == "pass" and outcomes[p["sid"]] == "refuse"
    ]
    assert set(outcomes.values()) <= {"greeting", "pass", "refuse", "undecided"}


RULES: dict[str, tuple[str, list[str], list[str]]] = {
    # rule: (outcome and reason prefix, positives, negatives)
    "rule 1": (
        "greeting",
        ["hi", "Hello!", "good morning :)", "thanks a lot", "안녕하세요"],
        [
            "hi, we run two salons",
            "hello can you write me a poem",
            "thanks for nothing, robot",
            "morning shift is chaos",
            "hey what is the setup fee",
        ],
    ),
    "rule 2": (
        "pass",
        [
            "what does the crm connector cost?",
            "our receptionist books every appointment",
            "rename this session please",
            "we use a spreadsheet for it",
            "tell me a joke, then send me the report",
        ],
        [
            "the salon is busy",
            "my son can't find a job",
            "what is the capital of peru",
            "write me a haiku about autumn",
            "how are you today",
        ],
    ),
    "rule 3": (
        "pass",
        [
            "35 a day",
            "about 40 calls",
            "40ish per week",
            "3 vans",
            "we see 40-50 customers",
        ],
        [
            "we have two vans and that's it",
            "I was born in 1990",
            "route 66 is a road",
            "3 is my lucky number",
            "my top 10 movies",
        ],
    ),
    "rule 4": (
        "refuse",
        [
            "write me a poem about the sea",
            "tell me a joke",
            "write a python script that sorts a list",
            "do my homework please",
            "translate this sentence into french: the cat sleeps",
        ],
        [
            "every recipe card gets typed up by hand",
            "I know it sounds like a joke",
            "write a poem about our phone intake",
            "tell me a joke and how much is the setup fee",
            "same story every time",
        ],
    ),
    "rule 6": (
        "undecided",
        [
            "how much would that cost?",
            "yes",
            "it's a mess honestly",
            "we do not use anything like that",
            "what is the capital of peru",
        ],
        [
            "hi",
            "tell me a joke",
            "35 calls a day",
            "the crm connector",
            "our customers book in by phone",
        ],
    ),
}


@pytest.mark.parametrize("rule", sorted(RULES))
def test_every_rule_has_five_positive_and_five_negative_examples(rule):
    gate = default_stage1()
    outcome, pos, neg = RULES[rule]
    assert len(pos) >= 5 and len(neg) >= 5
    for text in pos:
        got, reason = gate.classify(text)
        assert got == outcome, (text, got, reason)
        if rule != "rule 6":
            assert reason.startswith(rule), (text, reason)
    for text in neg:
        got, reason = gate.classify(text)
        assert not (got == outcome and (rule == "rule 6" or reason.startswith(rule))), (
            text,
            got,
            reason,
        )


def test_weak_terms_count_by_lemma_and_one_weak_term_is_not_a_pass():
    gate = default_stage1()
    assert gate.classify("my customer and the customers")[0] == "undecided"
    assert gate.classify("customers and invoices")[0] == "pass"


def test_lexicon_terms_are_disjoint_and_lower_case():
    lex = load_lexicon()
    Stage1(lex)  # raises on a term in both strengths or not lower-case
    bad = dict(lex, trade_words=[*lex["trade_words"], "Module"])
    with pytest.raises(ValueError):
        Stage1(bad)


# ---------------------------------------------------------------- stage 2 request and parser


def _protocol_rubric() -> tuple[str, str, str]:
    text = (ROOT / "evals" / "protocols" / "m5g-gate.md").read_text(encoding="utf-8")
    block = re.search(
        r"```text\ninstructions:\n(.*?)\n\ncriteria:\nyes: (.*?)\nno: (.*?)\n```",
        text,
        re.S,
    )
    assert block is not None
    return block.group(1), block.group(2), block.group(3)


def _ws(s: str) -> str:
    return " ".join(s.split())


def test_rubric_is_the_frozen_protocol_text():
    instr, yes, no = _protocol_rubric()
    assert _ws(INSTRUCTIONS) == _ws(instr)
    assert _ws(CRITERIA["true"]) == _ws(yes)
    assert _ws(CRITERIA["false"]) == _ws(no)


def test_state_is_message_recent_turns_and_scope_summary_only():
    turns = [
        {"role": "owner", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "owner", "text": "third"},
    ]
    st = build_state("how much?", turns)
    assert set(st) == {
        "message",
        "recent_turns",
        "scope_summary",
    }  # no session_title (D145)
    assert st["recent_turns"] == [
        {"role": "assistant", "text": "second"},
        {"role": "owner", "text": "third"},
    ]
    assert st["scope_summary"] == SCOPE_SUMMARY
    req = build_request(st)
    assert req["model"] == JEV_MODEL and set(req) == {"model", "state", "questions"}
    q = req["questions"][QUESTION_KEY]
    assert q["type"] == "noul" and set(q["criteria"]) == {"true", "false"}
    assert "session_title" not in json.dumps(req)
    assert state_hash(st) == state_hash(json.loads(json.dumps(st)))


def test_parser_reads_documented_and_tolerated_shapes_and_rejects_the_rest():
    cases = json.loads(
        (FIXTURES / "decisions_responses.json").read_text(encoding="utf-8")
    )["cases"]
    assert len(cases) >= 10
    for c in cases:
        if c["p"] is None:
            with pytest.raises(ValueError):
                parse_response(c["raw"])
        else:
            assert parse_response(c["raw"]) == pytest.approx(c["p"]), c["name"]


class FakeHttp:
    """Stands in for httpx; records the call. A real network call cannot happen (sockets are blocked)."""

    def __init__(self, status: int, body: Any) -> None:
        self.status, self.body, self.calls = status, body, []

    def post(self, url, *, json, headers, timeout):  # noqa: A002
        self.calls.append({"url": url, "json": json, "headers": headers})
        body, status = self.body, self.status

        class R:
            status_code = status

            @staticmethod
            def json():
                return body

        return R()


def test_decisions_client_posts_the_body_and_records_model_and_cost():
    doc = json.loads(
        (FIXTURES / "decisions_responses.json").read_text(encoding="utf-8")
    )["cases"][0]["raw"]
    http = FakeHttp(200, doc)
    client = OpenRouterDecisions("k", http=http)
    res = JevGate(client).ask("we have no crm", [], SCOPE_SUMMARY)
    assert (
        res.p == 0.96
        and res.model == JEV_MODEL
        and res.cost_usd == pytest.approx(0.00004116)
    )
    call = http.calls[0]
    assert call["url"] == OPENROUTER_DECISIONS_URL
    assert call["headers"]["Authorization"] == "Bearer k"
    assert call["json"]["state"]["message"] == "we have no crm"


def test_http_error_and_bad_body_fail_open():
    for http in (FakeHttp(500, {}), FakeHttp(200, {"answers": {}})):
        jev = JevGate(OpenRouterDecisions("k", http=http))
        d = Gate(jev=jev, thresholds=_th(0.2, 0.8)).decide("is it any good")
        assert (
            d.route == "band" and d.gate_failed and not d.refused and d.model_calls == 1
        )


class ReplayTransport:
    """Stage 2 from recorded raw responses keyed by state hash; fails if asked for anything not recorded."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.by_hash = {r["state_hash"]: r for r in records}
        self.last_response_model: str | None = None
        self.last_cost_usd: float | None = None
        self.calls = 0

    def decide(self, body):
        self.calls += 1
        rec = self.by_hash[state_hash(body["state"])]
        if rec.get("failure", "") and "TimeoutError" in rec["failure"]:
            raise TimeoutError("recorded timeout")
        self.last_response_model = rec.get("model")
        self.last_cost_usd = rec.get("cost_usd")
        return rec["raw"]


def test_stage2_replays_from_the_recorded_file():
    records = _jsonl(DRY_REPLAY)
    assert records
    transport = ReplayTransport(records)
    gate = Gate(jev=JevGate(transport), thresholds=NO_THRESHOLDS)
    by_sid = {r["sid"]: r for r in DEV + PROBES}
    for rec in records:
        row = by_sid[rec["sid"]]
        d = gate.decide(row["text"], row["recent_turns"])
        assert d.stage == 2 and d.state_hash == rec["state_hash"]
        assert d.p == rec["p"] and d.gate_failed == rec["gate_failed"]
        assert not d.refused  # before the dev run, stage 2 never refuses
    assert transport.calls == len(records)


# ---------------------------------------------------------------- thresholds and routing


def _th(t_refuse: float, t_pass: float) -> Thresholds:
    return Thresholds(
        t_refuse, t_pass, "s-out", "s-in", rubric_version=route_mod.RUBRIC_VERSION
    )


def test_threshold_rule_with_the_equality_case():
    rows = [
        ("i1", "in_scope", 0.40),
        ("i2", "in_scope", 0.90),
        ("o1", "out_of_scope", 0.40),  # equal to min_in: not a refusal threshold (D147)
        ("o2", "out_of_scope", 0.30),
        ("o3", "out_of_scope", 0.05),
    ]
    th = compute_thresholds(rows)
    assert (th.t_refuse, th.t_refuse_sid) == (0.30, "o2")
    assert (th.min_in, th.min_in_sid, th.max_out, th.max_out_sid) == (
        0.40,
        "i1",
        0.40,
        "o1",
    )
    assert (th.t_pass, th.t_pass_sid) == (0.90, "i2")
    assert th.band_width == pytest.approx(0.60)
    for _, label, p in rows:
        if label == "in_scope":
            assert route_p(p, th) != "refuse"
    assert (
        route_p(0.30, th) == "refuse"
        and route_p(0.35, th) == "band"
        and route_p(0.9, th) == "pass"
    )


def test_threshold_rule_without_separation_refuses_nothing():
    th = compute_thresholds(
        [
            ("i1", "in_scope", 0.1),
            ("o1", "out_of_scope", 0.5),
            ("o2", "out_of_scope", 0.2),
        ]
    )
    assert th.t_refuse == 0.0 and th.t_refuse_sid is None and not th.refuses
    assert th.t_pass == 1.0 and th.t_pass_sid is None
    assert route_p(0.0, th) == "band" and route_p(1.0, th) == "band"


def test_thresholds_absent_means_band_only(tmp_path):
    assert load_thresholds(tmp_path / "missing.json") is NO_THRESHOLDS
    if not route_mod.GATE_DEV_PATH.exists():
        assert route_mod.THRESHOLDS is NO_THRESHOLDS
    for p in (0.0, 0.01, 0.5, 0.99, 1.0):
        assert route_p(p, NO_THRESHOLDS) == "band"
    f = tmp_path / "gate_dev.json"
    f.write_text(json.dumps({"thresholds": _th(0.2, 0.7).as_dict()}))
    th = load_thresholds(f)
    assert (th.t_refuse, th.t_pass, th.refuses) == (0.2, 0.7, True)


def test_no_stage2_client_fails_open():
    d = Gate(jev=None, thresholds=_th(0.5, 0.9)).decide("it's a mess honestly")
    assert (
        d.route == "band" and d.gate_failed and d.model_calls == 0 and d.cost_usd == 0.0
    )


# ---------------------------------------------------------------- the discovery call site


class ScriptedJev:
    def __init__(self, p: float, cost: float = 0.00004) -> None:
        self.p, self.cost, self.calls = p, cost, 0
        self.last_response_model: str | None = None
        self.last_cost_usd: float | None = None

    def decide(self, body):
        self.calls += 1
        self.last_response_model, self.last_cost_usd = JEV_MODEL, self.cost
        return {
            "model": JEV_MODEL,
            "answers": {QUESTION_KEY: {"type": "noul", "noul": self.p}},
            "usage": {"cost": self.cost},
        }


class NeverChat:
    price_usd_per_mtok = (1.0, 2.0)
    calls = 0

    def invoke(self, messages, tools=None):
        raise AssertionError("a refused or greeted input must not call the chat model")


def _discovery(chat: Any, gate: Gate | None = None):
    return build_discovery_app(chat, HashEmbedder(), FakeBackend(), gate=gate)


def test_stage1_refusal_makes_no_model_call_and_costs_zero():
    app = _discovery(NeverChat())
    res = app.ask("write me a poem about the sea", "owner-a", "t1")
    assert res.answer == SCOPE_REPLY and res.error is None and res.cost_usd == 0.0
    gate_traces = [t for t in app.recent_traces() if t.get("node") == "gate"]
    assert gate_traces and gate_traces[0]["detail"]["route"] == "refuse"
    assert (
        gate_traces[0]["detail"]["cost_usd"] == 0.0
        and gate_traces[0]["detail"]["gate_calls"] == 0
    )


def test_stage2_refusal_makes_no_chat_call_and_costs_one_gate_call():
    jev = ScriptedJev(0.02)
    gate = Gate(jev=JevGate(jev), thresholds=_th(0.1, 0.9))
    app = _discovery(NeverChat(), gate)
    res = app.ask("what's the capital of peru", "owner-a", "t1")
    assert res.answer == SCOPE_REPLY and jev.calls == 1
    assert res.cost_usd == pytest.approx(0.00004)


def test_greeting_gets_the_fixed_reply_without_a_model_call():
    app = _discovery(NeverChat())
    res = app.ask("hello", "owner-a", "t1")
    assert (
        res.answer.startswith(GREETING_REPLY)
        and res.cost_usd == 0.0
        and res.error is None
    )


def test_pass_and_band_reach_the_chain():
    chat = ScriptedChat(
        [ChatReply('{"facts": [], "asks_about": "none"}', usage=Usage(10, 5))],
        default="Thanks. What is your company called?",
    )
    jev = ScriptedJev(0.5)
    gate = Gate(jev=JevGate(jev), thresholds=_th(0.1, 0.9))
    app = _discovery(chat, gate)
    res = app.ask("it's a mess honestly", "owner-a", "t1")  # undecided -> band -> chain
    assert jev.calls == 1 and chat.calls >= 1 and res.answer != SCOPE_REPLY
    calls_before = chat.calls
    app.ask(
        "what does the crm connector cost?", "owner-a", "t1"
    )  # stage-1 pass, no Jev call
    assert jev.calls == 1 and chat.calls > calls_before


def test_follow_up_sends_the_last_two_turns_as_text():
    seen: list[dict[str, Any]] = []

    class Spy(ScriptedJev):
        def decide(self, body):
            seen.append(body["state"])
            return super().decide(body)

    chat = ScriptedChat(default="Which tools do you use for bookings?")
    gate = Gate(jev=JevGate(Spy(0.95)), thresholds=_th(0.1, 0.9))
    app = _discovery(chat, gate)
    app.ask("what does the crm connector cost?", "owner-a", "t1")
    app.ask("yes", "owner-a", "t1")
    state = seen[-1]
    assert state["message"] == "yes" and len(state["recent_turns"]) == 2
    assert [t["role"] for t in state["recent_turns"]] == ["owner", "assistant"]
    assert set(state) == {"message", "recent_turns", "scope_summary"}


def test_contract_mode_skips_the_gate(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("contract mode must not call the gate")

    monkeypatch.setattr(Gate, "decide", boom)
    chat = ScriptedChat(default="ok")
    app = build_app(chat, HashEmbedder(), FakeBackend())
    res = app.ask("write me a poem about the sea", "owner-a", "t1")
    assert res.answer  # contract mode answered (abstain or reply) without the gate


# ---------------------------------------------------------------- SR callable and runner guards


def test_sr_gate_refuses_without_milo_live_and_matches_the_contract(monkeypatch):
    monkeypatch.delenv("MILO_LIVE", raising=False)
    with pytest.raises(RuntimeError):
        sr_gate("hi", recent_turns=[], scope_summary="x")
    d = Gate(jev=JevGate(ScriptedJev(0.01)), thresholds=_th(0.1, 0.9)).decide(
        "is it any good"
    )
    out = decision_dict(d)
    assert {
        "stage1",
        "model_calls",
        "refused",
        "gate_failed",
        "p",
        "band",
        "jev_request",
        "jev_model",
        "rubric_version",
        "state_hash",
        "d108_reply_check_passed",
        "cost_usd",
        "latency_s",
    } <= set(out)
    assert (
        out["refused"] is True
        and out["stage1"] == "undecided"
        and out["model_calls"] == 1
    )
    s1 = decision_dict(Gate(jev=None).decide("tell me a joke"))
    assert s1["stage1"] == "refuse" and s1["refused"] and s1["model_calls"] == 0


def _runner():
    spec = importlib.util.spec_from_file_location(
        "run_gate", ROOT / "evals" / "gate" / "run_gate.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_runner_live_and_final_refuse_without_their_flags(monkeypatch, capsys):
    rg = _runner()
    monkeypatch.delenv("MILO_LIVE", raising=False)
    monkeypatch.delenv("MILO_FINAL", raising=False)
    assert rg.main(["--live", "--reason", "x"]) == 2
    monkeypatch.setenv("MILO_LIVE", "1")
    assert rg.main(["--final", "--reason", "x"]) == 2
    assert rg.main(["--live"]) == 2  # no reason
    assert rg.main(["--live", "--reason", "x", "--max-cost", "5"]) == 2
    with pytest.raises(PermissionError):
        rg.scope_rows("test")


def test_runner_replay_matches_and_detects_a_change(tmp_path):
    rg = _runner()
    assert rg.replay(rg.DRY_PATH) == []
    doc = json.loads(rg.DRY_PATH.read_text(encoding="utf-8"))
    row = next(r for r in doc["rows"] if r.get("p") is not None)
    row["p"] = 0.123456
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(doc))
    assert rg.replay(bad)


def test_live_run_stops_after_a_failed_first_call():
    rg = _runner()
    gate = Gate(
        jev=JevGate(OpenRouterDecisions("k", http=FakeHttp(200, {"answers": {}})))
    )
    rows, recs, stop = rg.run_rows(
        gate, rg.gate_inputs("dev", with_probes=False), fail_fast=True
    )
    assert stop and stop.startswith("first stage-2 call failed") and len(recs) == 1


def test_cost_falls_back_to_input_tokens_times_price():
    body = {
        "answers": {QUESTION_KEY: {"type": "noul", "noul": 0.4}},
        "usage": {"input_tokens": 1000},
    }
    client = OpenRouterDecisions(
        "k", http=FakeHttp(200, body), price_in_usd_per_mtok=0.042
    )
    client.decide(build_request(build_state("x")))
    assert (
        client.last_cost_usd == pytest.approx(0.000042) and client.last_cost_estimated
    )


# ---------------------------------------------------------------- fix cycle 1

MIXED_ONE_WEAK = [
    "tell me a joke, and also how do you handle our bookings?",
    "write me a poem, then tell me what happens to our invoices",
    "tell me a joke about my customers",
    "write me a story about the salon",
]


@pytest.mark.parametrize("text", MIXED_ONE_WEAK)
def test_a_refuse_pattern_with_one_weak_term_goes_to_stage2_not_refused(text):
    gate, sim = default_stage1(), _sim()
    got = gate.classify(text)
    assert got[0] == "undecided" and got[1].startswith("one weak"), got
    assert sim.classify(text) == got
    jev = ScriptedJev(0.9)
    d = Gate(jev=JevGate(jev), thresholds=_th(0.1, 0.8)).decide(text)
    assert not d.refused and jev.calls == 1


def test_rubric_mismatch_refuses_the_thresholds(tmp_path):
    f = tmp_path / "gate_dev.json"
    stale = dict(_th(0.2, 0.7).as_dict(), rubric_version="m5g-rubric.v0")
    f.write_text(json.dumps({"thresholds": stale}))
    assert load_thresholds(f) is NO_THRESHOLDS
    with pytest.raises(route_mod.StaleThresholds):
        load_thresholds(f, strict=True)


def test_final_refuses_out_and_unknown_cost(monkeypatch, tmp_path):
    rg = _runner()
    monkeypatch.setenv("MILO_LIVE", "1")
    monkeypatch.setenv("MILO_FINAL", "1")
    with pytest.raises(SystemExit, match="--out"):
        rg.main(["--final", "--reason", "x", "--out", str(tmp_path / "x.json")])
    dev = tmp_path / "gate_dev.json"
    th = dict(_th(0.2, 0.7).as_dict(), rubric_version=route_mod.RUBRIC_VERSION)
    dev.write_text(
        json.dumps({"thresholds": th, "metrics": {"mean_cost_per_call_usd": 0.0}})
    )
    monkeypatch.setattr(rg, "DEV_PATH", dev)
    monkeypatch.setattr(rg, "TEST_PATH", tmp_path / "gate_test.json")
    with pytest.raises(SystemExit, match="unknown or 0"):
        rg.main(["--final", "--reason", "x"])


def test_replay_reads_no_test_rows(monkeypatch):
    rg = _runner()
    monkeypatch.delenv("MILO_FINAL", raising=False)
    seen: list[str] = []
    real = rg.scope_rows

    def spy(split):
        seen.append(split)
        return real(split)

    monkeypatch.setattr(rg, "scope_rows", spy)
    assert rg.replay(rg.DRY_PATH) == []
    assert seen == ["dev"]


def test_build_gate_is_live_with_a_key_and_stage1_only_without(tmp_path):
    from milo.gate import build_gate
    from milo.settings import Settings
    from milo.spend import SpendLedger

    assert build_gate(Settings()).jev is None
    ledger = SpendLedger(tmp_path / "spend.jsonl", budget_usd=5.0)
    gate = build_gate(Settings(openrouter_api_key="k", budget_usd=5.0), ledger=ledger)
    assert gate.jev is not None
    client = gate.jev.transport
    assert isinstance(client, OpenRouterDecisions) and client._ledger is ledger
    client._http = FakeHttp(
        200,
        {
            "model": JEV_MODEL,
            "answers": {QUESTION_KEY: {"type": "noul", "noul": 0.5}},
            "usage": {"cost": 0.00003},
        },
    )
    gate.decide("it's a mess honestly")
    assert ledger.spent() == pytest.approx(0.00003)
