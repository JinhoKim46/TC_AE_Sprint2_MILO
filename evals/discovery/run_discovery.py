"""Discovery evaluation (SPEC 10.4, 17 M5b; protocol `evals/protocols/m5b-discovery.md`).

Runs the dev conversations A-1, B-1 and E-1 (D149) through `TurnRunner` and the discovery chain, one fresh
in-memory account per conversation, and scores every turn against `<id>.expected.json`.

Modes
  --dry     a scripted model: the extract call returns `evals/discovery/dry_script.json` for the turn, the reply
            call asks the next question and, on purpose, quotes a price when the owner mentions one. Offline.
            Writes evals/results/discovery/discovery_dry.json.
  --replay  recompute every metric from the stored rows (default file: the dry result, or --file) and compare
            with the stored metrics; exit 1 on a difference. Offline. `make eval-discovery` runs this.
  (none)    live: MILO_LIVE=1, --model, --price-in, --price-out, --max-cost and --reason (HARNESS rule 14). The run
            stops and writes nothing when the running cost passes --max-cost. Run by the integrator only.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.common import final_mode, provenance  # noqa: E402
from milo.app import build_discovery_app  # noqa: E402
from milo.chain import answer_has_figure  # noqa: E402
from milo.facts.merge import evidence_is_verbatim  # noqa: E402
from milo.facts.store import SessionFacts, owner_hex  # noqa: E402
from milo.prompts.discovery import prompt_paths  # noqa: E402
from milo.protocol.loader import DEFAULT_PROTOCOL_PATH, get_protocol  # noqa: E402
from milo.protocol.validators import norm_key  # noqa: E402
from milo.rag.embed import FakeEmbedder  # noqa: E402
from milo.rag.ingest import load_kb  # noqa: E402
from milo.store import memory_repositories  # noqa: E402
from milo.tools import MiloBackend  # noqa: E402
from milo.turns import TurnRunner  # noqa: E402
from milo.types import ChatReply, ToolCall, Usage  # noqa: E402

CONV_DIR = ROOT / "data" / "demo" / "conversations"
COMPANIES = ROOT / "data" / "demo" / "companies"
UPLOAD = ROOT / "data" / "demo" / "uploads" / "ironbridge-procedure.txt"
KB_DIR = ROOT / "data" / "kb"
DRY_SCRIPT = Path(__file__).with_name("dry_script.json")
RESULTS_DIR = ROOT / "evals" / "results" / "discovery"
DRY_PATH = RESULTS_DIR / "discovery_dry.json"
LIVE_PATH = RESULTS_DIR / "discovery_live.json"
DEV = ("A-1", "B-1", "E-1")
TEST = ("A-2", "B-2", "E-2")
RETRIEVAL_MIN_SCORE = (
    0.10  # hashed embedder in this eval; retrieval is measured by M1, not here
)
BOOTSTRAP_RESAMPLES = 2000
SEED = 0
PRICE_WORDS = re.compile(r"\b(price|prices|cost|costs|estimate|fee|fees|quote)\b", re.I)


OWNER_PAUSE_S = 61.0  # simulated time between owner messages, so the D131 per-minute limit is not the measurement


class SimClock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


class Abort(RuntimeError):
    """The live run passed --max-cost; nothing is written."""


# ---- data (sealed test conversations, D149) ----


def load_conversation(conv: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if conv in TEST and not final_mode():
        raise PermissionError(
            f"{conv} is a test conversation; only `make final-test` runs read it (D149)"
        )
    if conv not in DEV + TEST:
        raise ValueError(conv)
    msgs = [
        json.loads(line)
        for line in (CONV_DIR / f"{conv}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    expected = json.loads(
        (CONV_DIR / f"{conv}.expected.json").read_text(encoding="utf-8")
    )
    return msgs, expected


# ---- models ----


class DryChat:
    """Scripted model. Extract calls return the dry script for the current turn; reply calls ask the next question
    and quote a price when the owner mentions one (an adversarial probe of the reply check)."""

    price_usd_per_mtok = (0.0, 0.0)
    model = "dry-scripted"

    def __init__(self, script: dict[str, Any]) -> None:
        self.script = script
        self.turn_key: tuple[str, int] = ("", 0)
        self.calls: list[dict[str, Any]] = []

    def invoke(self, messages: list[dict[str, Any]], tools: Any = None) -> ChatReply:
        system = (
            messages[0]["content"]
            if messages and messages[0]["role"] == "system"
            else ""
        )
        chars_in = sum(len(str(m.get("content", ""))) for m in messages)
        if system.startswith("You extract facts"):
            conv, turn = self.turn_key
            entry = self.script["conversations"][conv][str(turn)]
            body = {"facts": entry["facts"], "asks_about": entry["asks_about"]}
            content = json.dumps(body, ensure_ascii=False)
            kind = "extract"
        else:
            owner = str(messages[-1].get("content", ""))
            match = re.search(r"next question \([^)]*\): (.*)", system)
            question = match.group(1).strip() if match else "What else should I know?"
            if question.startswith("none:"):
                question = "Everything I need is recorded; you can review the specification and submit it."
            content = "Thank you, that is noted. "
            if PRICE_WORDS.search(owner):
                content += "It comes to about $1,450 a month for this. "
            content += question
            kind = "reply"
        reply = ChatReply(content, [], Usage(chars_in // 4, len(content) // 4))
        self.calls.append(
            {
                "kind": kind,
                "input_tokens": chars_in // 4,
                "output_tokens": len(content) // 4,
            }
        )
        return reply


class RecordingChat:
    """Live model wrapper: records every reply in the current row and enforces --max-cost."""

    def __init__(self, inner: Any, max_cost: float) -> None:
        self.inner = inner
        self.price_usd_per_mtok = inner.price_usd_per_mtok
        self.model = getattr(inner, "model", "unknown")
        self.max_cost = max_cost
        self.total = 0.0
        self.calls: list[dict[str, Any]] = []

    def invoke(self, messages: list[dict[str, Any]], tools: Any = None) -> ChatReply:
        if self.total > self.max_cost:
            raise Abort(
                f"running cost {self.total:.4f} passed --max-cost {self.max_cost}"
            )
        reply = self.inner.invoke(messages, tools)
        cost = getattr(self.inner, "last_cost_usd", None)
        if cost is None:
            pin, pout = self.price_usd_per_mtok
            cost = (
                reply.usage.input_tokens * pin + reply.usage.output_tokens * pout
            ) / 1_000_000
        self.total += float(cost)
        system = (
            messages[0]["content"]
            if messages and messages[0]["role"] == "system"
            else ""
        )
        self.calls.append(
            {
                "kind": "extract"
                if system.startswith("You extract facts")
                else "reply",
                "content": reply.content,
                "tool_calls": [
                    {"id": c.id, "name": c.name, "args": c.args}
                    for c in reply.tool_calls
                ],
                "input_tokens": reply.usage.input_tokens,
                "output_tokens": reply.usage.output_tokens,
                "cost_usd": float(cost),
            }
        )
        if self.total > self.max_cost:
            raise Abort(
                f"running cost {self.total:.4f} passed --max-cost {self.max_cost}"
            )
        return reply


# ---- running one conversation ----


def run_conversation(conv: str, chat: Any) -> list[dict[str, Any]]:
    msgs, expected = load_conversation(conv)
    protocol = get_protocol()
    repos = memory_repositories()
    facts = SessionFacts(repos, protocol)
    backend = MiloBackend(retriever=None, store=facts)  # type: ignore[arg-type]
    app = build_discovery_app(chat, FakeEmbedder(), backend, session_facts=facts)
    app.retriever.min_score = RETRIEVAL_MIN_SCORE
    app.ingest(load_kb(KB_DIR))
    backend.retriever = app.retriever
    clock = SimClock()
    runner = TurnRunner(app, spawn=lambda fn: fn(), clock=clock)
    user = str(expected["user_id"])
    owner = owner_hex(user)
    runner.runtime.ensure_owner(owner)
    repos.consents.record(owner, "interview", "v0")
    session_id = repos.sessions.create(owner)["id"]
    answered: list[str] = []
    rows = []
    for msg, exp in zip(msgs, expected["turns"], strict=True):
        n = int(msg["turn"])
        if hasattr(chat, "turn_key"):
            chat.turn_key = (conv, n)
        start = len(chat.calls)
        upload_path = exp.get("upload")
        upload = (ROOT / upload_path).read_bytes() if upload_path else None
        clock.now += OWNER_PAUSE_S
        turn_id = runner.submit(user, session_id, msg["owner_text"], upload=upload)
        events = runner.events(user, turn_id)
        rows.append(
            _row(
                conv,
                n,
                msg["owner_text"],
                exp,
                events,
                answered,
                chat.calls[start:],
                upload_path,
            )
        )
        for e in events:
            if (
                e["type"] == "fact_recorded"
                and e["payload"]["field_id"] not in answered
            ):
                answered.append(e["payload"]["field_id"])
    return rows


def _row(
    conv: str,
    n: int,
    owner_text: str,
    exp: dict[str, Any],
    events: list[dict[str, Any]],
    answered_before: list[str],
    calls: list[dict[str, Any]],
    upload_path: str | None,
) -> dict[str, Any]:
    recorded: dict[str, dict[str, Any]] = {}
    for e in events:
        if e["type"] == "fact_recorded":
            recorded[e["payload"]["field_id"]] = e["payload"]
    done = next(e["payload"] for e in events if e["type"] == "done")
    answer = next((e["payload"]["text"] for e in events if e["type"] == "answer"), "")
    estimates = [e["payload"] for e in events if e["type"] == "estimate_updated"]
    policies = [
        e["payload"]
        for e in events
        if e["type"] == "policy" and e["payload"].get("stage") == "input"
    ]
    flags = {
        k: v
        for k, v in exp.items()
        if k
        not in (
            "turn",
            "facts",
            "no_facts",
            "protocol_state",
            "estimate_status",
            "number_allowed",
            "next_question_id",
            "assumptions",
        )
    }
    return {
        "conversation": conv,
        "turn": n,
        "owner_text": owner_text,
        "upload": upload_path,
        "expected": {
            "facts": exp["facts"],
            "protocol_state": exp["protocol_state"],
            "estimate_status": exp["estimate_status"],
            "number_allowed": exp["number_allowed"],
            "next_question_id": exp["next_question_id"],
            "assumptions": exp.get("assumptions", []),
            "flags": flags,
        },
        "actual": {
            "facts": [
                {
                    k: v
                    for k, v in p.items()
                    if k
                    in (
                        "field_id",
                        "value",
                        "status",
                        "unit",
                        "period",
                        "evidence_text",
                        "source",
                        "previous_status",
                        "origin",
                    )
                }
                for p in recorded.values()
            ],
            "rejected": [e["payload"] for e in events if e["type"] == "fact_rejected"],
            "view": done.get("view"),
            "answer": answer,
            "estimate_events": estimates,
            "sources": [
                e["payload"]["doc_id"] for e in events if e["type"] == "source"
            ],
            "taint": policies[0]["taint"] if policies else None,
            "model_calls": done.get("model_calls", 0),
            "cost_usd": round(float(done.get("cost_usd", 0.0)), 8),
            "reply_check": done.get("reply_check", []),
            "error": done.get("error"),
        },
        "answered_before": list(answered_before),
        "calls": calls,
    }


# ---- scoring (pure; replay recomputes it from stored rows) ----


def _norm(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        return norm_key(value)
    if isinstance(value, list):
        items = [_norm(v) for v in value]
        if all(isinstance(i, str) for i in items):
            return sorted(set(items))
        return items
    return value


def fact_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    if (
        expected["field_id"] != actual["field_id"]
        or expected["status"] != actual["status"]
    ):
        return False
    if _norm(expected["value"]) != _norm(actual["value"]):
        return False
    for key in ("unit", "period"):
        if expected.get(key) is not None and expected[key] != actual.get(key):
            return False
    return True


def score_row(
    row: dict[str, Any], questions: dict[str, str], upload_text: str
) -> dict[str, Any]:
    exp, act = row["expected"], row["actual"]
    expected_facts = exp["facts"]
    actual_facts = act["facts"]
    tp = sum(1 for e in expected_facts if any(fact_matches(e, a) for a in actual_facts))
    by_field = {a["field_id"]: a for a in actual_facts}
    status_pairs = [
        (e["status"], by_field[e["field_id"]]["status"])
        for e in expected_facts
        if e["field_id"] in by_field
    ]
    view = act["view"] or {}
    figure = any(
        ev.get("monthly") is not None for ev in act["estimate_events"]
    ) or answer_has_figure(act["answer"])
    sources = [row["owner_text"]] + ([upload_text] if row.get("upload") else [])
    verbatim = [
        evidence_is_verbatim(a.get("evidence_text"), sources) for a in actual_facts
    ]
    repeated = any(
        fid in questions and questions[fid] in act["answer"]
        for fid in row["answered_before"]
    )
    return {
        "tp": tp,
        "n_expected": len(expected_facts),
        "n_actual": len(actual_facts),
        "status_match": sum(1 for x, y in status_pairs if x == y),
        "status_total": len(status_pairs),
        "state_ok": int(view.get("protocol_state") == exp["protocol_state"]),
        "next_ok": int(view.get("next_question_id") == exp["next_question_id"]),
        "estimate_ok": int(view.get("estimate_status") == exp["estimate_status"]),
        "chain_number_allowed": bool(view.get("number_allowed")),
        "expected_number_allowed": bool(exp["number_allowed"]),
        "figure_shown": bool(figure),
        "verbatim": sum(verbatim),
        "facts_stored": len(verbatim),
        "rejected_not_verbatim": sum(
            1 for r in act["rejected"] if r.get("reason") == "evidence_not_verbatim"
        ),
        "repeated": int(repeated),
        "model_calls": int(act["model_calls"]),
        "cost_usd": float(act["cost_usd"]),
    }


def _ci(values: list[tuple[float, float]]) -> list[float] | None:
    """Bootstrap 95% CI of sum(num)/sum(den) over turns, 2000 resamples, seed 0 (D24)."""
    if not values:
        return None
    rng = random.Random(SEED)
    n = len(values)
    stats = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        den = sum(d for _, d in sample)
        stats.append(sum(x for x, _ in sample) / den if den else 0.0)
    stats.sort()
    return [
        round(stats[int(0.025 * BOOTSTRAP_RESAMPLES)], 4),
        round(stats[int(0.975 * BOOTSTRAP_RESAMPLES) - 1], 4),
    ]


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def _summary(
    rows: list[dict[str, Any]], scored: list[dict[str, Any]]
) -> dict[str, Any]:
    tp = sum(s["tp"] for s in scored)
    n_exp = sum(s["n_expected"] for s in scored)
    n_act = sum(s["n_actual"] for s in scored)
    precision, recall = _ratio(tp, n_act), _ratio(tp, n_exp)
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    not_allowed_chain = [s for s in scored if not s["chain_number_allowed"]]
    not_allowed_exp = [s for s in scored if not s["expected_number_allowed"]]
    calls = [s["model_calls"] for s in scored]
    return {
        "turns": len(scored),
        "fact_precision": precision,
        "fact_precision_ci95": _ci([(s["tp"], s["n_actual"]) for s in scored]),
        "fact_recall": recall,
        "fact_recall_ci95": _ci([(s["tp"], s["n_expected"]) for s in scored]),
        "fact_f1": f1,
        "status_accuracy": _ratio(
            sum(s["status_match"] for s in scored),
            sum(s["status_total"] for s in scored),
        ),
        "state_accuracy": _ratio(sum(s["state_ok"] for s in scored), len(scored)),
        "state_accuracy_ci95": _ci([(s["state_ok"], 1) for s in scored]),
        "next_question_accuracy": _ratio(
            sum(s["next_ok"] for s in scored), len(scored)
        ),
        "next_question_accuracy_ci95": _ci([(s["next_ok"], 1) for s in scored]),
        "estimate_status_accuracy": _ratio(
            sum(s["estimate_ok"] for s in scored), len(scored)
        ),
        "premature_estimate_rate": _ratio(
            sum(s["figure_shown"] for s in not_allowed_chain), len(not_allowed_chain)
        ),
        "premature_estimate_turns": len(not_allowed_chain),
        "premature_estimate_rate_vs_expected": _ratio(
            sum(s["figure_shown"] for s in not_allowed_exp), len(not_allowed_exp)
        ),
        "premature_estimate_turns_vs_expected": len(not_allowed_exp),
        "evidence_verbatim_rate": _ratio(
            sum(s["verbatim"] for s in scored), sum(s["facts_stored"] for s in scored)
        ),
        "facts_discarded_not_verbatim": sum(s["rejected_not_verbatim"] for s in scored),
        "repeated_question_rate": _ratio(
            sum(s["repeated"] for s in scored), len(scored)
        ),
        "model_calls_mean": _ratio(sum(calls), len(calls)),
        "model_calls_max": max(calls) if calls else 0,
        "cost_per_turn_usd": round(sum(s["cost_usd"] for s in scored) / len(scored), 8)
        if scored
        else None,
    }


def conflict_checks(rows: list[dict[str, Any]]) -> dict[str, bool]:
    """B-1 style checks, driven by the expected flags: the conflict is raised, then confirmed in code."""
    out: dict[str, bool] = {}
    for row in rows:
        exp, act = row["expected"], row["actual"]
        key = f"{row['conversation']}#{row['turn']}"
        view = act["view"] or {}
        if any(f["status"] == "conflicting" for f in exp["facts"]):
            out[key + ":conflict_raised"] = (
                view.get("protocol_state") == "quote_blocked"
                and str(view.get("next_question_id", "")).startswith("confirm_")
                and not any(
                    ev.get("monthly") is not None for ev in act["estimate_events"]
                )
                and any(a["status"] == "conflicting" for a in act["facts"])
            )
        if exp["flags"].get("answers_conflict"):
            fid = exp["flags"]["answers_conflict"]
            want = next(f for f in exp["facts"] if f["field_id"] == fid)
            out[key + ":confirmed_in_code"] = any(
                fact_matches(want, a) for a in act["facts"]
            )
    return out


def injection_checks(rows: list[dict[str, Any]], pricing: set[str]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for row in rows:
        flags = row["expected"]["flags"]
        if not (
            flags.get("injection") or flags.get("injected_line_must_not_change_state")
        ):
            continue
        act = row["actual"]
        out[f"{row['conversation']}#{row['turn']}"] = (
            act["taint"] == "high"
            and not any(a["field_id"] in pricing for a in act["facts"])
            and not any(a["status"] == "confirmed" for a in act["facts"])
            and not any(
                a.get("previous_status") not in (None, "unknown") for a in act["facts"]
            )
        )
    return out


def compute_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    protocol = get_protocol()
    questions = {f.id: f.question for f in protocol.fields}
    upload_text = UPLOAD.read_text(encoding="utf-8")
    scored = [score_row(r, questions, upload_text) for r in rows]
    per_conv = {}
    for conv in sorted({r["conversation"] for r in rows}):
        idx = [i for i, r in enumerate(rows) if r["conversation"] == conv]
        per_conv[conv] = _summary([rows[i] for i in idx], [scored[i] for i in idx])
    conflicts = conflict_checks(rows)
    injections = injection_checks(rows, set(protocol.pricing_field_ids))
    return {
        "pooled": _summary(rows, scored),
        "per_conversation": per_conv,
        "conflict_handling": conflicts,
        "conflict_handling_pass": all(conflicts.values()) if conflicts else None,
        "injection_handling": injections,
        "injection_handling_pass": all(injections.values()) if injections else None,
        "failed_turns": [
            f"{r['conversation']}#{r['turn']}"
            for r, s in zip(rows, scored, strict=True)
            if not (
                s["state_ok"]
                and s["next_ok"]
                and s["estimate_ok"]
                and s["tp"] == s["n_expected"] == s["n_actual"]
            )
        ],
    }


# ---- result files ----


def _inputs() -> dict[str, Path]:
    inputs: dict[str, Path] = {
        "protocol": DEFAULT_PROTOCOL_PATH,
        "upload": UPLOAD,
        "dry_script": DRY_SCRIPT,
    }
    for conv in DEV:
        inputs[f"{conv}.jsonl"] = CONV_DIR / f"{conv}.jsonl"
        inputs[f"{conv}.expected.json"] = CONV_DIR / f"{conv}.expected.json"
    for company in ("a", "b"):
        inputs[f"company_{company}"] = COMPANIES / f"{company}.yaml"
    for path in prompt_paths():
        inputs[f"prompt_{path.name}"] = path
    return inputs


def build_result(
    rows: list[dict[str, Any]], *, mode: str, model: str, command: str, reason: str
) -> dict[str, Any]:
    doc = provenance(
        model=model, seed=SEED, temperature=0.0, command=command, inputs=_inputs()
    )
    doc.update(
        mode=mode,
        why_this_run=reason,
        conversations=list(DEV),
        metrics=compute_metrics(rows),
        rows=rows,
    )
    return doc


def write(doc: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, indent=1, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def run_dry(out: Path) -> dict[str, Any]:
    script = json.loads(DRY_SCRIPT.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for conv in DEV:
        rows += run_conversation(conv, DryChat(script))
    doc = build_result(
        rows,
        mode="dry",
        model=DryChat.model,
        command="uv run python evals/discovery/run_discovery.py --dry",
        reason="offline harness and code-gate check: a scripted model; no external model was needed",
    )
    write(doc, out)
    return doc


def run_live(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("MILO_LIVE") != "1":
        raise SystemExit(
            "live mode needs MILO_LIVE=1 (the integrator's live batch, SPEC D126)"
        )
    missing = [
        n
        for n in ("model", "price_in", "price_out", "max_cost", "reason")
        if getattr(args, n) is None
    ]
    if missing:
        raise SystemExit(
            "live mode also needs: "
            + ", ".join("--" + m.replace("_", "-") for m in missing)
        )
    from milo.llm.client import build_chat
    from milo.settings import Settings
    from milo.spend import SpendLedger

    settings = Settings.from_env()
    ledger = SpendLedger(ROOT / "logs" / "spend.jsonl", budget_usd=settings.budget_usd)
    ledger.check(args.max_cost)
    inner = build_chat(
        args.model,
        (args.price_in, args.price_out),
        settings=settings,
        ledger=ledger,
        purpose="m5b-discovery",
    )
    chat = RecordingChat(inner, args.max_cost)
    rows: list[dict[str, Any]] = []
    try:
        for conv in DEV:
            rows += run_conversation(conv, chat)
    except Abort as exc:
        raise SystemExit(f"stopped, nothing written: {exc}") from None
    command = (
        f"MILO_LIVE=1 uv run python evals/discovery/run_discovery.py --model {args.model} "
        f"--price-in {args.price_in} --price-out {args.price_out} --max-cost {args.max_cost} --reason ..."
    )
    doc = build_result(
        rows, mode="live", model=args.model, command=command, reason=args.reason
    )
    doc["live_cost_usd"] = round(chat.total, 6)
    write(doc, args.out or LIVE_PATH)
    return doc


def replay(path: Path) -> int:
    doc = json.loads(path.read_text(encoding="utf-8"))
    again = json.loads(json.dumps(compute_metrics(doc["rows"]), default=str))
    if again != doc["metrics"]:
        print(f"replay MISMATCH for {path}")
        for key in sorted(set(again) | set(doc["metrics"])):
            if again.get(key) != doc["metrics"].get(key):
                print(
                    f"  {key}: stored {doc['metrics'].get(key)!r} recomputed {again.get(key)!r}"
                )
        return 1
    pooled = again["pooled"]
    print(
        f"replay OK {path.name}: {pooled['turns']} turns, fact F1 {pooled['fact_f1']}, "
        f"state accuracy {pooled['state_accuracy']}, premature-estimate rate {pooled['premature_estimate_rate']}, "
        f"evidence verbatim rate {pooled['evidence_verbatim_rate']}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry", action="store_true")
    mode.add_argument("--replay", action="store_true")
    ap.add_argument(
        "--file", type=Path, help="result file to replay (default: the dry result)"
    )
    ap.add_argument("--out", type=Path, help="where to write the result")
    ap.add_argument("--model")
    ap.add_argument("--price-in", type=float, dest="price_in")
    ap.add_argument("--price-out", type=float, dest="price_out")
    ap.add_argument("--max-cost", type=float, dest="max_cost")
    ap.add_argument("--reason")
    args = ap.parse_args(argv)
    if args.replay:
        return replay(args.file or DRY_PATH)
    if args.dry:
        doc = run_dry(args.out or DRY_PATH)
        pooled = doc["metrics"]["pooled"]
        print(
            f"dry run: {pooled['turns']} turns, fact F1 {pooled['fact_f1']}, "
            f"premature-estimate rate {pooled['premature_estimate_rate']}, "
            f"failed turns {doc['metrics']['failed_turns']}"
        )
        return 0
    doc = run_live(args)
    print(
        f"live run: fact F1 {doc['metrics']['pooled']['fact_f1']}, cost {doc['live_cost_usd']} USD"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ToolCall", "compute_metrics", "load_conversation", "main"]
