"""Tool-selection evaluation (SPEC 17 M3, section 8, D56, D126).

Runs `data/tool_cases.jsonl` through the real tool loop with `MiloBackend` and scores the calls the model emitted
in its first reply against `expected_calls` (`match: exact` compares names and arguments, `match: names` compares
the multiset of names). The loop, schema check, policy and backend all run; knowledge search returns no chunks,
and the model gets no second paid call, so a live run costs one call per case.

Modes
  --dry     a scripted model emits each case's expected calls. Offline; checks the harness, not a model.
            Writes evals/results/agent/tools_dry.json.
  --replay  recompute every metric from the stored rows of evals/results/agent/tools_*.json (or --file) and
            compare with the stored metrics. Offline; exit 1 on a difference.
  (none)    live: needs MILO_LIVE=1, --model, --price-in, --price-out and --reason (why the external model is
            needed, HARNESS rule 14). Not run by the M3 builder; the integrator runs it after M5a merges.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.common import file_sha256, provenance  # noqa: E402
from milo.loop import run_turn  # noqa: E402
from milo.store.memory import MemoryApprovals  # noqa: E402
from milo.tools import MiloBackend  # noqa: E402
from milo.tools.args import clean_args  # noqa: E402
from milo.tools.specs import InvalidToolArguments, validate_args  # noqa: E402
from milo.trace import Tracer  # noqa: E402
from milo.types import ChatModel, ChatReply, Source, ToolCall, Usage  # noqa: E402

CASES_PATH = ROOT / "data" / "tool_cases.jsonl"
RESULTS_DIR = ROOT / "evals" / "results" / "agent"
DRY_PATH = RESULTS_DIR / "tools_dry.json"
LIVE_PATH = RESULTS_DIR / "tools_live.json"
BOOTSTRAP_RESAMPLES = 2000
SEED = 0


class NullIndex:
    """Knowledge search with no chunks: the eval scores the emitted call, not the retrieval."""

    def search(
        self, query: str, *, filters: dict[str, Any] | None = None
    ) -> list[Source]:
        return []


class FirstReply:
    """Wraps a chat model: the first reply is real, every later call returns plain text at no cost."""

    def __init__(self, inner: ChatModel) -> None:
        self.inner = inner
        self.price_usd_per_mtok = inner.price_usd_per_mtok
        self.first: ChatReply | None = None
        self.paid_cost: float = 0.0

    def invoke(self, messages: list[dict[str, Any]], tools: Any = None) -> ChatReply:
        if self.first is None:
            self.first = self.inner.invoke(messages, tools)
            return self.first
        return ChatReply(content="(eval: no second call)", usage=Usage(0, 0))


class ScriptedExpected:
    """Dry-mode model: emits the case's expected calls verbatim, or a plain reply when none are expected."""

    price_usd_per_mtok = (0.0, 0.0)

    def __init__(self, expected: list[dict[str, Any]]) -> None:
        self.expected = expected

    def invoke(self, messages: list[dict[str, Any]], tools: Any = None) -> ChatReply:
        calls = [
            ToolCall(f"dry{i}", c["name"], json.loads(json.dumps(c["args"])))
            for i, c in enumerate(self.expected)
        ]
        return ChatReply(
            content="" if calls else "Understood.", tool_calls=calls, usage=Usage(0, 0)
        )


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ---- scoring (pure; replay recomputes it from stored rows) ----


def names_of(calls: list[dict[str, Any]]) -> list[str]:
    return [c["name"] for c in calls]


def score_row(row: dict[str, Any]) -> dict[str, Any]:
    expected, emitted = row["expected_calls"], row["emitted_calls"]
    names_equal = sorted(names_of(expected)) == sorted(names_of(emitted))
    exact_equal = len(expected) == len(emitted) and all(
        e["name"] == m["name"] and e["args"] == m["args"]
        for e, m in zip(expected, emitted, strict=True)
    )
    passed = exact_equal if row["match"] == "exact" else names_equal
    return {"names_equal": names_equal, "exact_equal": exact_equal, "passed": passed}


def bootstrap_ci(values: list[int]) -> tuple[float, float]:
    """Bootstrap 95% CI of the mean, 2000 resamples, seed 0 (SPEC D24)."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(SEED)
    n = len(values)
    means = sorted(
        sum(values[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(BOOTSTRAP_RESAMPLES)
    )
    return (
        round(means[int(0.025 * BOOTSTRAP_RESAMPLES)], 4),
        round(means[int(0.975 * BOOTSTRAP_RESAMPLES) - 1], 4),
    )


def rate(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def compute_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [score_row(r) for r in rows]
    no_call = [r for r in rows if not r["expected_calls"]]
    some_call = [r for r in rows if r["expected_calls"]]
    exact_rows = [s for r, s in zip(rows, scored, strict=True) if r["match"] == "exact"]
    emitted = [c for r in rows for c in r["emitted_calls"]]
    passes = [int(s["passed"]) for s in scored]
    low, high = bootstrap_ci(passes)
    return {
        "cases": len(rows),
        "case_pass_rate": rate(sum(passes), len(rows)),
        "case_pass_rate_ci95": [low, high],
        "name_match_rate": rate(sum(s["names_equal"] for s in scored), len(rows)),
        "exact_args_rate_on_exact_cases": rate(
            sum(s["exact_equal"] for s in exact_rows), len(exact_rows)
        ),
        "false_call_rate": rate(
            sum(1 for r in no_call if r["emitted_calls"]), len(no_call)
        ),
        "missed_call_rate": rate(
            sum(1 for r in some_call if not r["emitted_calls"]), len(some_call)
        ),
        "emitted_calls": len(emitted),
        "schema_valid_rate": rate(
            sum(c["schema_valid"] for c in emitted), len(emitted)
        ),
        "expected_schema_invalid_cases": sorted(
            r["id"] for r in rows if not r["expected_schema_valid"]
        ),
        "failed_cases": sorted(
            r["id"] for r, s in zip(rows, scored, strict=True) if not s["passed"]
        ),
    }


# ---- running ----


def run_case(case: dict[str, Any], chat: ChatModel, system: str) -> dict[str, Any]:
    backend = MiloBackend(retriever=NullIndex())
    wrapped = FirstReply(chat)
    result = run_turn(
        chat=wrapped,
        tools=backend,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": case["query"]},
        ],
        user_id=case["user_id"],
        thread_id="eval",
        turn_id=f"eval-{case['id']}",
        untrusted_texts=[case["query"]],
        approvals=MemoryApprovals(),
        approval_scope=f"{case['user_id']}:eval",
        tracer=Tracer(),
    )
    specs = {s.name: s for s in backend.specs()}
    first = wrapped.first.tool_calls if wrapped.first else []

    def valid(name: str, args: dict[str, Any]) -> bool:
        if name not in specs:
            return False
        try:
            validate_args(specs[name], args)
        except InvalidToolArguments:
            return False
        return True

    emitted = []
    for call in first:
        args = clean_args(call.args) if isinstance(call.args, dict) else {}
        emitted.append(
            {"name": call.name, "args": args, "schema_valid": valid(call.name, args)}
        )
    expected = case["expected_calls"]
    return {
        "id": case["id"],
        "user_id": case["user_id"],
        "query": case["query"],
        "match": case["match"],
        "expected_calls": expected,
        "expected_schema_valid": all(valid(c["name"], c["args"]) for c in expected),
        "emitted_calls": emitted,
        "policy_decisions": [t.policy_decision for t in result.tool_results],
        "backend_state_changed": any(e.state_changed for e in backend.executed),
        "stopped_for_approval": result.stopped,
        "cost_usd": result.cost_usd,
    }


def system_prompt() -> str:
    from milo.app import SYSTEM_PROMPT

    return SYSTEM_PROMPT


def run_rows(chat_for: Any, system: str) -> list[dict[str, Any]]:
    rows = []
    for case in load_cases():
        row = run_case(case, chat_for(case), system)
        row.update(score_row(row))
        rows.append(row)
    return rows


def write_result(
    path: Path,
    *,
    mode: str,
    model: str,
    temperature: Any,
    command: str,
    reason: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    doc = {
        **provenance(
            model=model,
            seed=SEED,
            temperature=temperature,
            command=command,
            inputs={"tool_cases": CASES_PATH},
        ),
        "mode": mode,
        "why_this_run": reason,
        "metrics": compute_metrics(rows),
        "rows": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return doc


def run_dry(out: Path = DRY_PATH) -> dict[str, Any]:
    rows = run_rows(
        lambda case: ScriptedExpected(case["expected_calls"]), system_prompt()
    )
    return write_result(
        out,
        mode="dry",
        model="scripted-expected-calls",
        temperature=0.0,
        command="uv run python evals/agent/run_tools.py --dry",
        reason="offline harness check: a scripted model emits the expected calls; no external model was needed",
        rows=rows,
    )


def run_live(args: argparse.Namespace) -> dict[str, Any]:
    import os

    from milo.llm.cloud import OpenRouterChat
    from milo.settings import Settings
    from milo.spend import SpendLedger

    if os.environ.get("MILO_LIVE") != "1":
        raise SystemExit(
            "live mode needs MILO_LIVE=1 (the integrator runs it after M5a merges); "
            "use --dry for the offline harness check or --replay for the stored rows"
        )
    missing = [
        n
        for n in ("model", "price_in", "price_out", "reason")
        if getattr(args, n) in (None, "")
    ]
    if missing:
        raise SystemExit(
            "live mode also needs: "
            + ", ".join(f"--{m.replace('_', '-')}" for m in missing)
        )
    cfg = Settings.from_env()
    ledger = SpendLedger(budget_usd=cfg.budget_usd)
    chat = OpenRouterChat(
        args.model,
        cfg.openrouter_api_key,
        (args.price_in, args.price_out),
        ledger=ledger,
        purpose="eval-tools",
    )
    rows = run_rows(lambda case: chat, system_prompt())
    return write_result(
        args.out or LIVE_PATH,
        mode="live",
        model=args.model,
        temperature="provider_default",
        command="MILO_LIVE=1 uv run python evals/agent/run_tools.py --model "
        + args.model,
        reason=args.reason,
        rows=rows,
    )


# ---- replay ----


def replay(files: list[Path]) -> int:
    if not files:
        print(
            "replay: no stored result under evals/results/agent/ (run --dry first)",
            file=sys.stderr,
        )
        return 1
    status = 0
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = doc["rows"]
        for row in rows:
            fresh = score_row(row)
            for key, value in fresh.items():
                if row[key] != value:
                    print(
                        f"MISMATCH {path.name} {row['id']} {key}: stored {row[key]} recomputed {value}"
                    )
                    status = 1
        metrics = compute_metrics(rows)
        if metrics != doc["metrics"]:
            for key in sorted(set(metrics) | set(doc["metrics"])):
                if metrics.get(key) != doc["metrics"].get(key):
                    print(
                        f"MISMATCH {path.name} metric {key}: stored {doc['metrics'].get(key)} recomputed {metrics.get(key)}"
                    )
            status = 1
        stored_sha = doc.get("inputs", {}).get("tool_cases")
        if stored_sha != file_sha256(CASES_PATH):
            print(
                f"WARNING {path.name}: data/tool_cases.jsonl changed since this run; "
                "rows were re-scored against their own stored expectations; rerun the eval to refresh"
            )
        if status == 0:
            m = doc["metrics"]
            print(
                f"replay OK {path.name}: {m['cases']} cases, pass rate {m['case_pass_rate']} "
                f"(95% CI {m['case_pass_rate_ci95'][0]} to {m['case_pass_rate_ci95'][1]}), mode {doc['mode']}"
            )
    return status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry", action="store_true")
    mode.add_argument("--replay", action="store_true")
    ap.add_argument(
        "--file", type=Path, help="replay: one result file instead of tools_*.json"
    )
    ap.add_argument("--out", type=Path, help="where to write the result (dry or live)")
    ap.add_argument("--model")
    ap.add_argument("--price-in", type=float)
    ap.add_argument("--price-out", type=float)
    ap.add_argument("--reason")
    args = ap.parse_args(argv)
    if args.replay:
        files = [args.file] if args.file else sorted(RESULTS_DIR.glob("tools_*.json"))
        return replay(files)
    if args.dry:
        doc = run_dry(args.out or DRY_PATH)
        m = doc["metrics"]
        print(
            f"dry run: {m['cases']} cases, pass rate {m['case_pass_rate']}, failed {m['failed_cases']}"
        )
        return 0
    doc = run_live(args)
    print(f"live run: pass rate {doc['metrics']['case_pass_rate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
