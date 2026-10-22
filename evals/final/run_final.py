"""F2: the single, sealed test-split run (SPEC 10.1, 10.5, section 17; HARNESS rule 6; plan docs/final-run-plan.md).

    MILO_LIVE=1 make final-test          (sets MILO_FINAL=1) runs the next pending steps of the plan
    uv run python evals/final/run_final.py --replay      replays every stored test result (no network, no key)

Steps, in the plan's order; each refuses to run again once its result file exists, so the test split is read once:
    m1           M1 dense retrieval on the test rows (run_retrieval.py with split=test); M2 has no adopted arm, so
                 the adopted configuration is dense and this run stands for M2 too (m2-ab.md)
    gate         M5G `run_gate.py --final` (protocol run 4)
    e02          E02 step 7: task A on A-2, B-2, E-2 and task B on the test gold, chosen model and reference only
    m7-generate  M7 product answers on a 30-question test sample (the dev sampler's rule), then the labelling packet
    discovery    M5b `run_discovery.py --final` on A-2, B-2, E-2
    m7-judge     the two dev judges on the labelled test answers; waits until evals/rag/labels_test.jsonl is committed

The gold file must match MANIFEST.json (`common.gold_sha256()`) before anything is read. The log
evals/results/test_access.log gets `started` per test load (common.py), `failed <step>` on an error,
`awaiting-labels` when the first invocation stops before the judge, and exactly one `ok` at the end.
Code, prompts, thresholds, models and prices are the dev ones; nothing here tunes anything.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evals import common  # noqa: E402

RES = ROOT / "evals" / "results"
RETRIEVAL_TEST = RES / "retrieval" / "retrieval_test.json"
GATE_TEST = RES / "m5g" / "gate_test.json"
CHAT_TEST = RES / "e02" / "chat_test.json"
SAMPLE_TEST = ROOT / "evals" / "rag" / "sample_test.json"
GEN_TEST = RES / "m7" / "generation_test.json"
LABEL_PACKET = RES / "m7" / "label_packet_test.json"
LABELS_TEST = ROOT / "evals" / "rag" / "labels_test.jsonl"
JUDGE_TEST = RES / "m7" / "judge_test.json"
DISCOVERY_TEST = RES / "discovery" / "discovery_test.json"
JUDGE_DEV = RES / "m7" / "judge_dev.json"

F2_CAP_USD = 2.50
PRODUCT_MODEL, PRODUCT_PRICE = (
    "anthropic/claude-sonnet-5.5",
    (2.0, 10.0),
)  # dev runs, D159 low effort
CHOSEN = (
    "claude-haiku-5.5",
    "anthropic/claude-haiku-5.5",
)  # E02 dev decision (chat_dev.json)
JUDGES = [
    ("anthropic/claude-haiku-5.5", (0.10, 0.50)),
    ("openai/gpt-4.1-mini", (0.40, 1.60)),
]  # judge_dev.json
CAPS = {
    "m1": 0.01,
    "gate": 0.05,
    "e02": 0.30,
    "m7-generate": 0.40,
    "discovery": 1.00,
    "m7-judge": 0.10,
}
REASONS = {
    "m1": "F2: the chosen embedding model's test numbers, reported once (D5, e01-rerun.md)",
    "gate": "stage-2 probability needed to set or confirm a threshold",
    "e02": "F2: E02 step 7, the chosen model and the reference on the test split, once",
    "m7-generate": "F2: product answers for the labelled test sample (D126)",
    "discovery": "product model extraction through the discovery chain is the measurement (F2, test conversations)",
    "m7-judge": "judge models are the thing measured against the labels (D126), test split",
}


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def _load(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def step_cost(step: str) -> float:
    """Actual cost of a finished step, from its own result file (0 when it has none yet)."""
    files = {
        "m1": (RETRIEVAL_TEST, lambda d: d["total_cost_usd"]),
        "gate": (GATE_TEST, lambda d: d["metrics"]["stage2_cost_usd"]),
        "e02": (CHAT_TEST, lambda d: d["chat_cost_usd"] + d["embedding_cost_usd"]),
        "m7-generate": (GEN_TEST, lambda d: d["total_cost_usd"]),
        "discovery": (DISCOVERY_TEST, lambda d: d["live_cost_usd"]),
        "m7-judge": (JUDGE_TEST, lambda d: d["total_cost_usd"]),
    }
    path, get = files[step]
    return float(get(_load(path))) if path.exists() else 0.0


def spent() -> float:
    return round(sum(step_cost(s) for s in CAPS), 6)


def budget_ok(step: str) -> bool:
    total = spent() + CAPS[step]
    if total > F2_CAP_USD:
        print(
            f"skip {step}: spent {spent():.4f} + cap {CAPS[step]} > F2 cap {F2_CAP_USD}",
            file=sys.stderr,
        )
        return False
    return True


# ---------------------------------------------------------------- steps


def run_m1() -> None:
    from evals.retrieval import run_retrieval as rr

    args = argparse.Namespace(
        dry=False, chunk="800/100", split="test", rows=common.load_gold("test"), reason=REASONS["m1"],
        command="MILO_FINAL=1 MILO_LIVE=1 python evals/final/run_final.py (step m1: run_retrieval.py, split test)",
    )  # fmt: skip
    if rr.run(args) != 0:
        raise RuntimeError("m1 returned non-zero")


def run_sub(step: str, cmd: list[str], out: Path) -> None:
    proc = subprocess.run(
        [sys.executable, *cmd], cwd=ROOT, env=dict(os.environ), check=False
    )
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(
            f"{step}: exit {proc.returncode}, {_rel(out)} {'exists' if out.exists() else 'missing'}"
        )


def run_gate() -> None:
    run_sub(
        "gate",
        [
            "evals/gate/run_gate.py",
            "--final",
            "--max-cost",
            str(CAPS["gate"]),
            "--reason",
            REASONS["gate"],
        ],
        GATE_TEST,
    )


def run_discovery() -> None:
    run_sub(
        "discovery",
        [
            "evals/discovery/run_discovery.py", "--final", "--model", PRODUCT_MODEL,
            "--price-in", str(PRODUCT_PRICE[0]), "--price-out", str(PRODUCT_PRICE[1]),
            "--max-cost", str(CAPS["discovery"]), "--reason", REASONS["discovery"],
        ],
        DISCOVERY_TEST,
    )  # fmt: skip


def run_e02() -> None:
    from evals.chat import compare_chat as cc
    from milo.llm import cloud
    from milo.rag.embed import OpenRouterEmbedder
    from milo.settings import Settings
    from milo.spend import SpendLedger

    cfg = Settings.from_env()
    key = cfg.openrouter_api_key
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    verified = cc.verify_slugs(
        cloud.fetch_catalogue(key), candidates={CHOSEN[0]: CHOSEN[1]}
    )
    if CHOSEN[1] not in verified["pinned"]:
        raise RuntimeError(f"{CHOSEN[1]} is not usable in the catalogue")
    convs = cc.load_conversations("test")
    gold = common.load_gold("test")
    plan = cc.planned_cost(verified["pinned"], convs, len(gold))
    if plan > CAPS["e02"]:
        raise RuntimeError(
            f"planned E02 test cost {plan:.4f} USD is above the step cap {CAPS['e02']}"
        )
    ledger = SpendLedger(ROOT / "logs" / "spend.jsonl", budget_usd=cfg.budget_usd)
    ledger.check(plan)
    embedder = OpenRouterEmbedder(key, cc.EMBEDDING_MODEL, price_usd_per_mtok=0.2)
    items = cc.task_b_context(gold, embedder)
    rows: list[dict[str, Any]] = []
    for slug, p in verified["pinned"].items():
        chat = cloud.OpenRouterChat(
            slug,
            key,
            (p["prompt"] * 1e6, p["completion"] * 1e6),
            ledger=ledger,
            purpose="e02-final",
        )
        rows += cc.task_a_rows(chat, slug, convs, time.sleep)
        rows += cc.task_b_rows(chat, slug, items, time.sleep)
    code = cc.write_result(
        argparse.Namespace(split="test", result=None), rows, verified, plan,
        round(embedder.total_cost_usd, 6),
        command="MILO_FINAL=1 MILO_LIVE=1 python evals/final/run_final.py (step e02: compare_chat.py task A and B, split test)",
    )  # fmt: skip
    if code != 0 or not CHAT_TEST.exists():
        raise RuntimeError("e02 did not write chat_test.json")


def run_m7_generate() -> None:
    from evals.rag import run_rag_eval as rag
    from evals.rag import sample as smp
    from evals.rag import schema

    if not SAMPLE_TEST.exists():
        test = common.load_gold("test")
        rows = smp.draw(test, split="test")
        strata: dict[str, int] = {}
        for r in rows:
            strata[r["type"]] = strata.get(r["type"], 0) + 1
        doc = {
            "split": "test", "seed": smp.SEED, "n": len(rows), "test_rows": len(test),
            "strata": dict(sorted(strata.items())), "gold_sha256": common.gold_sha256(), "rows": rows,
        }  # fmt: skip
        SAMPLE_TEST.write_text(
            json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    args = argparse.Namespace(
        product_model=PRODUCT_MODEL, price_in=PRODUCT_PRICE[0], price_out=PRODUCT_PRICE[1],
        max_cost=CAPS["m7-generate"], reason=REASONS["m7-generate"],
    )  # fmt: skip
    code = rag.live_generate(
        args, sample_path=SAMPLE_TEST, out_path=GEN_TEST,
        command=f"MILO_FINAL=1 MILO_LIVE=1 python evals/final/run_final.py (step m7-generate: run_rag_eval.py "
        f"generation, sample_test.json, --max-cost {CAPS['m7-generate']} --product-model {PRODUCT_MODEL})",
    )  # fmt: skip
    if code != 0 or not GEN_TEST.exists():
        raise RuntimeError(f"m7-generate: exit {code}")
    write_label_packet(schema)


def write_label_packet(schema: Any) -> None:
    """The labeller's only input: question, reference answer and quotes, contexts and answer, plus the empty template.
    The labeller never reads the gold file itself (SPEC 10.1)."""
    gold = {r["qid"]: r for r in common.load_gold("test")}
    gen_rows = _load(GEN_TEST)["rows"]
    counts = {g["qid"]: len(g["contexts"]) for g in gen_rows}
    packet = {
        "split": "test",
        "generation": _rel(GEN_TEST),
        "generation_sha256": common.file_sha256(GEN_TEST),
        "rubric": "evals/rag/schema.py and evals/protocols/m7-judge.md (Procedure, metric table)",
        "write_to": _rel(LABELS_TEST),
        "template": schema.template_rows([g["qid"] for g in gen_rows], counts),
        "rows": [
            {
                "qid": g["qid"],
                "type": g["type"],
                "question": g["question"],
                "reference_answer": gold[g["qid"]].get("reference_answer"),
                "gold_quotes": [
                    e.get("quote") for e in gold[g["qid"]].get("gold_evidence") or []
                ],
                "contexts": g["contexts"],
                "answer": g["answer"],
                "abstained": g["abstained"],
            }  # fmt: skip
            for g in gen_rows
        ],
    }
    LABEL_PACKET.write_text(
        json.dumps(packet, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {_rel(LABEL_PACKET)} for the labelling agent")


def labels_ready() -> list[str]:
    """Problems that keep the judge from running: labels missing, invalid or not committed."""
    from evals.rag import schema

    if not GEN_TEST.exists():
        return ["generation_test.json is missing"]
    gen_rows = _load(GEN_TEST)["rows"]
    counts = {g["qid"]: len(g["contexts"]) for g in gen_rows}
    _, problems = schema.validate_labels(
        LABELS_TEST, [g["qid"] for g in gen_rows], counts
    )
    if problems:
        return problems
    log = subprocess.run(
        ["git", "log", "--format=%H", "--", _rel(LABELS_TEST)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", _rel(LABELS_TEST)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if not log or dirty:
        return [
            "labels_test.jsonl must be committed (alone, before the judge run) with no local change"
        ]
    return []


def run_m7_judge() -> None:
    from evals.rag import judge as jd
    from evals.rag import run_rag_eval as rag
    from evals.rag import schema

    problems = labels_ready()
    if problems:
        raise RuntimeError("; ".join(problems[:5]))
    templates = jd.load_templates(rag.PROMPTS)
    dev_inputs = _load(JUDGE_DEV)["inputs"]
    for name in jd.PROMPT_NAMES.values():
        if common.file_sha256(rag.PROMPTS / name) != dev_inputs.get(f"prompt/{name}"):
            raise RuntimeError(
                f"judge prompt {name} differs from the dev run's; the prompts are frozen"
            )
    gen_rows = _load(GEN_TEST)["rows"]
    counts = {g["qid"]: len(g["contexts"]) for g in gen_rows}
    labels, _ = schema.validate_labels(
        LABELS_TEST, [g["qid"] for g in gen_rows], counts
    )
    judges = {
        slug: rag.live_judge_fn(rag._chat(slug, price, "m7-judge-final"))
        for slug, price in JUDGES
    }
    rows = rag.assemble(gen_rows, labels)
    partial = jd.run_judges(rows, judges, templates, CAPS["m7-judge"])
    inputs: dict[str, Path | str] = {
        "gold": common.GOLD_PATH, "sample": SAMPLE_TEST, "generation": GEN_TEST, "labels": LABELS_TEST,
    }  # fmt: skip
    for n in jd.PROMPT_NAMES.values():
        inputs[f"prompt/{n}"] = rag.PROMPTS / n
    doc = rag.result_doc(
        rows,
        model="judges=" + ",".join(judges),
        command="MILO_FINAL=1 MILO_LIVE=1 python evals/final/run_final.py (step m7-judge: run_rag_eval.py judge, "
        f"--max-cost {CAPS['m7-judge']} " + " ".join(f"--judge {s},{p[0]},{p[1]}" for s, p in JUDGES) + ")",
        inputs=inputs,
        extra={
            "dry": False, "partial": partial, "split": "test", "spend_reason": REASONS["m7-judge"],
            "labels_file": _rel(LABELS_TEST), "sample_file": _rel(SAMPLE_TEST),
            "judge_prices_usd_per_mtok": {s: list(p) for s, p in JUDGES},
        },
    )  # fmt: skip
    doc["total_cost_usd"] = round(
        sum(v["cost_usd"] for v in doc["summary"].values()), 8
    )
    rag._write(JUDGE_TEST, doc)
    print(
        f"wrote {_rel(JUDGE_TEST)}: {len(rows)} rows, cost {doc['total_cost_usd']} USD, partial={partial}"
    )
    rag.print_table(doc)
    if partial:
        raise RuntimeError("m7-judge stopped at its cap (partial)")


STEPS: list[tuple[str, Path, Any]] = [
    ("m1", RETRIEVAL_TEST, run_m1),
    ("gate", GATE_TEST, run_gate),
    ("e02", CHAT_TEST, run_e02),
    ("m7-generate", GEN_TEST, run_m7_generate),
    ("discovery", DISCOVERY_TEST, run_discovery),
    ("m7-judge", JUDGE_TEST, run_m7_judge),
]


# ---------------------------------------------------------------- replay


def replay(final: bool) -> int:
    jobs = [
        (
            RETRIEVAL_TEST,
            [
                "evals/retrieval/run_retrieval.py",
                "--replay",
                "--result",
                _rel(RETRIEVAL_TEST),
            ],
        ),
        (
            CHAT_TEST,
            ["evals/chat/compare_chat.py", "--replay", "--result", _rel(CHAT_TEST)],
        ),
        (
            GEN_TEST,
            ["evals/rag/run_rag_eval.py", "--replay", "--result", _rel(GEN_TEST)],
        ),
        (
            JUDGE_TEST,
            ["evals/rag/run_rag_eval.py", "--replay", "--result", _rel(JUDGE_TEST)],
        ),
        (
            DISCOVERY_TEST,
            [
                "evals/discovery/run_discovery.py",
                "--replay",
                "--file",
                _rel(DISCOVERY_TEST),
            ],
        ),
    ]
    if final:  # the gate replay re-reads the sealed test scope texts, so it runs only under MILO_FINAL=1
        jobs.append(
            (
                GATE_TEST,
                ["evals/gate/run_gate.py", "--replay", "--file", _rel(GATE_TEST)],
            )
        )
    elif GATE_TEST.exists():
        print(
            f"{_rel(GATE_TEST)}: not replayed here (needs the sealed test texts; replayed inside make final-test)"
        )
    ok, found = True, 0
    for path, cmd in jobs:
        if not path.exists():
            continue
        found += 1
        env = {k: v for k, v in os.environ.items() if k != "MILO_LIVE"}
        proc = subprocess.run(
            [sys.executable, *cmd],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-2:])
        print(f"{'ok' if proc.returncode == 0 else 'FAILED'} {_rel(path)}\n  {tail}")
        ok &= proc.returncode == 0
    if not found:
        print("no final (test-split) result yet")
    return 0 if ok else 1


# ---------------------------------------------------------------- main


def log_lines() -> list[str]:
    if not common.TEST_ACCESS_LOG.exists():
        return []
    return common.TEST_ACCESS_LOG.read_text(encoding="utf-8").splitlines()


def run() -> int:
    if not common.final_mode() or os.environ.get("MILO_LIVE") != "1":
        print(
            "refusing to run: F2 needs MILO_FINAL=1 (make final-test) and MILO_LIVE=1",
            file=sys.stderr,
        )
        return 2
    if any(line.split(" ", 1)[-1] == "ok" for line in log_lines()):
        print(
            "refusing to run: test_access.log already has an `ok` line; F2 runs once",
            file=sys.stderr,
        )
        return 2
    if not common.gold_matches_manifest():
        common.log_test_status(
            "failed gold-hash: gold SHA-256 differs from MANIFEST.json"
        )
        print(
            "refusing to run: the gold file's SHA-256 differs from MANIFEST.json",
            file=sys.stderr,
        )
        return 2
    for step, out, fn in STEPS:
        if out.exists():
            print(f"{step}: done ({_rel(out)} exists), cost {step_cost(step):.6f} USD")
            continue
        if step == "m7-judge" and labels_ready():
            common.log_test_status("awaiting-labels")
            print(
                f"stopped before m7-judge: label {_rel(LABEL_PACKET)} into {_rel(LABELS_TEST)}, commit it, rerun"
            )
            return 0
        if not budget_ok(step):
            common.log_test_status(f"failed {step}: F2 cap")
            return 1
        print(
            f"== {step} (cap {CAPS[step]} USD, F2 spent so far {spent():.6f} USD)",
            flush=True,
        )
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - every failure is logged before it propagates
            common.log_test_status(
                f"failed {step}: {type(exc).__name__}: {str(exc)[:200]}"
            )
            raise
        print(
            f"{step}: cost {step_cost(step):.6f} USD; F2 total {spent():.6f} USD",
            flush=True,
        )
    if replay(final=True) != 0:
        common.log_test_status("failed replay")
        return 1
    common.log_test_status("ok")
    print(f"F2 ok; total {spent():.6f} USD")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--replay",
        action="store_true",
        help="replay every stored test result (offline)",
    )
    args = ap.parse_args(argv)
    if args.replay:
        return replay(final=common.final_mode())
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
