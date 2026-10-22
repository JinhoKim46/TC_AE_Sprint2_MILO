"""M7 RAG evaluation with an LLM judge (protocol evals/protocols/m7-judge.md).

    python evals/rag/run_rag_eval.py --sample                  write evals/rag/sample_dev.json (30 dev qids, seed 26)
    python evals/rag/run_rag_eval.py --make-template [--generation F]   write evals/rag/labels.template.jsonl
    python evals/rag/run_rag_eval.py --check-labels            validate evals/rag/labels.jsonl against the sample
    python evals/rag/run_rag_eval.py --dry                     offline: scripted product and judges on the committed fixture
    python evals/rag/run_rag_eval.py --replay [--result F]     recompute every number from the stored rows
    MILO_LIVE=1 python evals/rag/run_rag_eval.py --generate --max-cost 0.40 --reason "..." \\
        --product-model <slug> --price-in <usd/Mtok> --price-out <usd/Mtok>
    MILO_LIVE=1 python evals/rag/run_rag_eval.py --judge --max-cost 0.50 --reason "..." \\
        --judge <slug>,<usd/Mtok in>,<usd/Mtok out> --judge <slug>,<in>,<out>

Order (D26): sample, generate, labels (separate labelling agent), judge prompts, judge. `--judge` refuses while a
prompt is a placeholder, while the labels are missing or invalid, or while git does not show the labels committed
before the prompts. Live modes refuse without MILO_LIVE=1, `--max-cost` and `--reason` (SPEC D126). The dev split is
read only through `evals/common.py`. Rows store every model reply (D56).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evals import common  # noqa: E402
from evals.rag import generate as gen  # noqa: E402
from evals.rag import judge as jd  # noqa: E402
from evals.rag import sample as smp  # noqa: E402
from evals.rag import schema  # noqa: E402
from evals.retrieval import run_retrieval as rr  # noqa: E402

RAG_DIR = ROOT / "evals" / "rag"
RESULTS = ROOT / "evals" / "results" / "m7"
SAMPLE = RAG_DIR / "sample_dev.json"
PROMPTS = RAG_DIR / "prompts"
FIXTURE = RAG_DIR / "fixtures" / "rag_dry_fixture.json"
GEN_RESULT = RESULTS / "generation_dev.json"
JUDGE_RESULT = RESULTS / "judge_dev.json"
DRY_RESULT = RESULTS / "rag_dry.json"
DRY_TEMPLATE = "DRY {{question}} || {{contexts}} || {{answer}}"
SEED = smp.SEED
LIVE_REFUSAL = (
    "refusing to run: a live M7 step calls OpenRouter models (SPEC D126) and needs MILO_LIVE=1, --max-cost and "
    "--reason ({need}); use --dry for the offline harness or --replay to recompute a stored result"
)


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def _write(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def sample_qids() -> list[str]:
    return [r["qid"] for r in _load(SAMPLE)["rows"]]


# ---------------------------------------------------------------- pipeline


def assemble(
    gen_rows: list[dict[str, Any]], labels: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_q = {r["qid"]: r for r in labels}
    return [
        {
            "qid": g["qid"],
            "type": g["type"],
            "question": g["question"],
            "contexts": g["contexts"],
            "answer": g["answer"],
            "abstained": g["abstained"],
            "labels": {
                "faithfulness": by_q[g["qid"]]["faithfulness"],
                "relevance": by_q[g["qid"]]["relevance"],
                "context_relevance": by_q[g["qid"]]["context_relevance"],
                "labeller": by_q[g["qid"]]["labeller"],
            },
        }
        for g in gen_rows
    ]


def scripted_judge(
    script: dict[str, dict[str, dict[str, str]]], slug: str
) -> jd.JudgeFn:
    def fn(qid: str, metric: str, prompt: str) -> jd.JudgeReply:
        return jd.JudgeReply(script[slug][qid][metric])

    return fn


def run_dry() -> dict[str, Any]:
    fx = _load(FIXTURE)
    gen_rows, _ = gen.generate(fx["questions"], gen.scripted_ask(fx["generation"]))
    for lab in fx["labels"]:
        n = next(len(g["contexts"]) for g in gen_rows if g["qid"] == lab["qid"])
        bad = schema.validate_row(lab, n, lab["qid"])
        if bad:
            raise SystemExit("fixture labels are invalid: " + "; ".join(bad))
    rows = assemble(gen_rows, fx["labels"])
    templates = {m: DRY_TEMPLATE for m in jd.METRICS}
    judges = {s: scripted_judge(fx["judges"], s) for s in fx["judges"]}
    jd.run_judges(rows, judges, templates)
    return result_doc(
        rows,
        model=f"scripted-product; judges={','.join(judges)}",
        command="python evals/rag/run_rag_eval.py --dry",
        inputs={"fixture": FIXTURE, "script": Path(__file__)},
        extra={"dry": True, "partial": False},
    )


def result_doc(
    rows: list[dict[str, Any]],
    *,
    model: str,
    command: str,
    inputs: dict[str, Path | str],
    extra: dict[str, Any],
) -> dict[str, Any]:
    for name in ("generate", "judge", "schema", "stats", "sample"):
        inputs[f"evals/rag/{name}.py"] = RAG_DIR / f"{name}.py"
    doc: dict[str, Any] = {
        **common.provenance(
            model=model, seed=SEED, temperature=0.0, command=command, inputs=inputs
        ),
        **extra,
        "bootstrap": {"resamples": 2000, "seed": 0},
        "rows": rows,
    }
    doc.update(jd.derive(rows))
    return doc


# ---------------------------------------------------------------- live


def _live_args_ok(args: argparse.Namespace, extra: list[str]) -> bool:
    need = ["max_cost", "reason", *extra]
    missing = [n for n in need if getattr(args, n) in (None, "", [])]
    if os.environ.get("MILO_LIVE") != "1" or missing:
        print(
            LIVE_REFUSAL.format(
                need=", ".join("--" + n.replace("_", "-") for n in need)
            ),
            file=sys.stderr,
        )
        return False
    return True


def _chat(slug: str, price: tuple[float, float], purpose: str) -> Any:
    from milo.llm.cloud import OpenRouterChat
    from milo.settings import Settings

    key = Settings.from_env().openrouter_api_key
    if not key:
        raise SystemExit(
            "refusing to run: OPENROUTER_API_KEY is not set in the environment"
        )
    return OpenRouterChat(slug, key, price, purpose=purpose, temperature=0.0)


def live_generate(
    args: argparse.Namespace,
    sample_path: Path = SAMPLE,
    out_path: Path = GEN_RESULT,
    command: str | None = None,
) -> int:
    """Product answers for a sample. F2 passes the test sample and `generation_test.json`; the code is the same."""
    if not _live_args_ok(args, ["product_model", "price_in", "price_out"]):
        return 2
    if not sample_path.exists():
        print(
            f"refusing to run: {_rel(sample_path)} is missing; run --sample and commit it first",
            file=sys.stderr,
        )
        return 2
    from milo.app import build_app
    from milo.rag.embed import live_embedder
    from milo.llm.cloud import DEFAULT_EMBEDDING_MODEL
    from milo.rag.chunk import DEFAULT_OVERLAP, DEFAULT_SIZE
    from milo.rag.ingest import INDEX_ROOT, kb_sha256
    from milo.rag.retrieve import Retriever
    from milo.rag.vectors import VectorStore
    from milo.tools import MiloBackend

    # `make index` writes to data/index/<model>-<size>-<overlap>/ (ingest.py); read the same folder.
    index_dir = (
        INDEX_ROOT
        / f"{DEFAULT_EMBEDDING_MODEL.replace('/', '_')}-{DEFAULT_SIZE}-{DEFAULT_OVERLAP}"
    )
    store = VectorStore(index_dir) if index_dir.exists() else None
    manifest = store.read_manifest() if store else None
    if not manifest or manifest.get("kb_sha256") != kb_sha256(rr.KB):
        print(
            "refusing to run: the index is missing or stale; run `make index` first",
            file=sys.stderr,
        )
        return 2
    assert store is not None and manifest is not None
    embedder = live_embedder()
    chat = _chat(args.product_model, (args.price_in, args.price_out), "m7-generate")
    retriever = Retriever(embedder, store=store)
    app = build_app(
        chat, embedder, MiloBackend(retriever=retriever), vector_store=retriever
    )
    sample = _load(sample_path)
    rows, partial = gen.generate(sample["rows"], gen.app_ask(app), float(args.max_cost))
    inputs: dict[str, Path | str] = {"gold": common.GOLD_PATH, "sample": sample_path}
    doc = {
        **common.provenance(
            model=f"chat={args.product_model}; embedding={embedder.model}",
            seed=SEED,
            temperature=0.0,
            command=command
            or f"MILO_LIVE=1 python evals/rag/run_rag_eval.py --generate --max-cost {args.max_cost} "
            f"--product-model {args.product_model}",
            inputs={**inputs, "evals/rag/generate.py": RAG_DIR / "generate.py"},
        ),
        "dry": False,
        "partial": partial,
        "spend_reason": args.reason,
        "sample_file": _rel(sample_path),
        "pipeline": "milo.app.build_app, contract mode, dense top 5",
        "index": {
            k: manifest.get(k)
            for k in (
                "model",
                "chunk_size",
                "overlap",
                "chunk_count",
                "chunk_sha256",
                "kb_sha256",
            )
        },
        "advanced_rag": os.environ.get("MILO_ADVANCED_RAG", "0"),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 8),
        "rows": rows,
    }
    _write(out_path, doc)
    print(
        f"wrote {_rel(out_path)}: {len(rows)} rows, cost {doc['total_cost_usd']} USD, partial={partial}"
    )
    return 1 if partial else 0


def parse_judges(specs: list[str]) -> list[tuple[str, tuple[float, float]]]:
    out = []
    for s in specs:
        slug, pin, pout = s.split(",")
        out.append((slug, (float(pin), float(pout))))
    return out


def live_judge_fn(chat: Any) -> jd.JudgeFn:
    from milo.errors import ProviderFailure
    from milo.llm.retry import invoke_with_retry

    def fn(qid: str, metric: str, prompt: str) -> jd.JudgeReply:
        try:
            reply = invoke_with_retry(chat, [{"role": "user", "content": prompt}], None)
        except ProviderFailure as exc:
            return jd.JudgeReply("", error=str(exc.error))
        cost = getattr(chat, "last_cost_usd", None)
        if cost is None:
            pin, pout = chat.price_usd_per_mtok
            cost = (
                reply.usage.input_tokens * pin + reply.usage.output_tokens * pout
            ) / 1e6
        return jd.JudgeReply(
            reply.content,
            reply.usage.input_tokens,
            reply.usage.output_tokens,
            float(cost),
        )

    return fn


def preflight() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], dict[str, str], list[str]
]:
    """Generation rows, valid labels and the final prompts; the problems found otherwise (D26 order included)."""
    problems: list[str] = []
    if not SAMPLE.exists() or not GEN_RESULT.exists():
        return [], [], {}, [f"{_rel(SAMPLE)} and {_rel(GEN_RESULT)} must exist first"]
    gen_rows = _load(GEN_RESULT)["rows"]
    counts = {g["qid"]: len(g["contexts"]) for g in gen_rows}
    labels, bad = schema.validate_labels(schema.LABELS, sample_qids(), counts)
    problems += bad
    templates = jd.load_templates(PROMPTS)
    problems += [
        f"{PROMPTS.name}/{jd.PROMPT_NAMES[m]} is still a placeholder (write it after the labels)"
        for m, t in templates.items()
        if jd.is_placeholder(t)
    ]
    if not problems:
        problems += jd.order_problems(
            ROOT, schema.LABELS, [PROMPTS / n for n in jd.PROMPT_NAMES.values()]
        )
    return gen_rows, labels, templates, problems


def live_judge(args: argparse.Namespace) -> int:
    if not _live_args_ok(args, ["judge"]):
        return 2
    if len(args.judge) != 2:
        print(
            "refusing to run: exactly two --judge <slug>,<price-in>,<price-out> are required (D26)",
            file=sys.stderr,
        )
        return 2
    gen_rows, labels, templates, problems = preflight()
    if problems:
        print("refusing to run: " + "; ".join(problems), file=sys.stderr)
        return 2
    judges = {
        slug: live_judge_fn(_chat(slug, price, "m7-judge"))
        for slug, price in parse_judges(args.judge)
    }
    rows = assemble(gen_rows, labels)
    partial = jd.run_judges(rows, judges, templates, float(args.max_cost))
    inputs: dict[str, Path | str] = {
        "gold": common.GOLD_PATH,
        "sample": SAMPLE,
        "generation": GEN_RESULT,
        "labels": schema.LABELS,
    }
    for m, n in jd.PROMPT_NAMES.items():
        inputs[f"prompt/{n}"] = PROMPTS / n
    doc = result_doc(
        rows,
        model="judges=" + ",".join(judges),
        command=f"MILO_LIVE=1 python evals/rag/run_rag_eval.py --judge --max-cost {args.max_cost} "
        + " ".join(f"--judge {j}" for j in args.judge),
        inputs=inputs,
        extra={
            "dry": False,
            "partial": partial,
            "spend_reason": args.reason,
            "judge_prices_usd_per_mtok": {
                s: list(p) for s, p in parse_judges(args.judge)
            },
        },
    )
    doc["total_cost_usd"] = round(
        sum(v["cost_usd"] for v in doc["summary"].values()), 8
    )
    _write(JUDGE_RESULT, doc)
    print(
        f"wrote {_rel(JUDGE_RESULT)}: {len(rows)} rows, cost {doc['total_cost_usd']} USD, partial={partial}"
    )
    print_table(doc)
    return 1 if partial else 0


# ---------------------------------------------------------------- offline steps


def cmd_sample() -> int:
    sample = smp.build()
    doc = {
        **common.provenance(
            model="none",
            seed=SEED,
            temperature=0.0,
            command="python evals/rag/run_rag_eval.py --sample",
            inputs={
                "gold": common.GOLD_PATH,
                "evals/rag/sample.py": RAG_DIR / "sample.py",
            },
        ),
        **sample,
    }
    _write(SAMPLE, doc)
    print(f"wrote {_rel(SAMPLE)}: {sample['n']} qids, strata {sample['strata']}")
    return 0


def cmd_template(args: argparse.Namespace) -> int:
    counts = None
    if args.generation:
        counts = {
            g["qid"]: len(g["contexts"]) for g in _load(Path(args.generation))["rows"]
        }
    rows = schema.template_rows(sample_qids(), counts)
    schema.TEMPLATE.write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    print(
        f"wrote {_rel(schema.TEMPLATE)}: {len(rows)} rows"
        + ("" if counts else " (no generation file: context slots empty)")
    )
    return 0


def cmd_check_labels() -> int:
    counts = None
    if GEN_RESULT.exists():
        counts = {g["qid"]: len(g["contexts"]) for g in _load(GEN_RESULT)["rows"]}
    _, problems = schema.validate_labels(schema.LABELS, sample_qids(), counts)
    for p in problems:
        print("  " + p, file=sys.stderr)
    print(
        "labels OK" if not problems else f"labels INVALID: {len(problems)} problem(s)",
        file=sys.stderr if problems else sys.stdout,
    )
    return 1 if problems else 0


def print_table(doc: dict[str, Any]) -> None:
    print(
        f"{'judge':<28}{'metric':<18}{'kappa':>7}  {'95% CI':<16}{'agree':>6}  validated"
    )
    for slug, s in doc["summary"].items():
        for m, a in s["metrics"].items():
            print(
                f"{slug:<28}{m:<18}{a['kappa']!s:>7}  {a['ci']!s:<16}{a['agreement']!s:>6}  {a['validated']}"
            )
    print(doc["decision"]["line"])


# ---------------------------------------------------------------- replay


def replay_judge_file(path: Path) -> bool:
    stored = _load(path)
    rows = json.loads(json.dumps(stored["rows"]))
    bad: list[str] = []
    for key, val in jd.derive(rows).items():
        rr._same(
            json.loads(json.dumps(stored[key])),
            json.loads(json.dumps(val)),
            0.0,
            key,
            bad,
        )
    note = "recomputed from rows"
    if stored.get("dry") and not bad:
        fresh = run_dry()
        rr._same(
            stored["rows"],
            json.loads(json.dumps(fresh["rows"])),
            0.0,
            "regenerated",
            bad,
        )
        note += "; rows regenerated from the fixture"
    elif not stored.get("dry"):
        # F2's test result names its own labels file; a dev result uses labels.jsonl
        labels = (
            ROOT / stored["labels_file"] if stored.get("labels_file") else schema.LABELS
        )
        if labels.exists() and common.file_sha256(labels) != stored["inputs"].get(
            "labels"
        ):
            bad.append(f"{labels.name} differs from the labels hashed into the result")
    print(f"{'MATCH' if not bad else 'MISMATCH'} {_rel(path)}: {note}")
    for line in bad[:20]:
        print("  ", line)
    return not bad


def replay_generation_file(path: Path) -> bool:
    stored = _load(path)
    bad = []
    qids = [r["qid"] for r in stored["rows"]]
    sample = ROOT / stored["sample_file"] if stored.get("sample_file") else SAMPLE
    if (
        sample.exists()
        and not stored.get("partial")
        and qids != [r["qid"] for r in _load(sample)["rows"]]
    ):
        bad.append("qids differ from the sample")
    for r in stored["rows"]:
        if r["abstained"] != (not r["contexts"]):
            bad.append(f"{r['qid']}: abstained flag disagrees with the contexts")
    print(
        f"{'MATCH' if not bad else 'MISMATCH'} {_rel(path)}: {len(qids)} generation rows checked"
    )
    for line in bad[:20]:
        print("  ", line)
    return not bad


def replay(args: argparse.Namespace) -> int:
    ok = True
    found = False
    if args.result:
        paths = [Path(args.result)]
    else:
        paths = [p for p in (GEN_RESULT, JUDGE_RESULT, DRY_RESULT) if p.exists()]
    for p in paths:
        found = True
        ok &= (
            replay_generation_file(p)
            if p.name.startswith("generation")
            else replay_judge_file(p)
        )
    if not found:
        print("no stored result to replay (evals/results/m7/*.json)", file=sys.stderr)
        return 1
    if schema.LABELS.exists() and SAMPLE.exists():
        counts = (
            {g["qid"]: len(g["contexts"]) for g in _load(GEN_RESULT)["rows"]}
            if GEN_RESULT.exists()
            else None
        )
        _, problems = schema.validate_labels(schema.LABELS, sample_qids(), counts)
        print(
            f"{'MATCH' if not problems else 'MISMATCH'} {_rel(schema.LABELS)}: schema check"
        )
        for line in problems[:20]:
            print("  ", line)
        ok &= not problems
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = ap.add_mutually_exclusive_group()
    for flag in (
        "sample",
        "make-template",
        "check-labels",
        "dry",
        "replay",
        "generate",
    ):
        mode.add_argument("--" + flag, action="store_true")
    ap.add_argument(
        "--generation", help="--make-template: the generation result (context counts)"
    )
    ap.add_argument("--result", help="--replay: one result file")
    ap.add_argument("--max-cost", type=float)
    ap.add_argument("--reason")
    ap.add_argument("--product-model")
    ap.add_argument("--price-in", type=float)
    ap.add_argument("--price-out", type=float)
    ap.add_argument(
        "--judge",
        action="append",
        default=[],
        help="live judge: <slug>,<usd/Mtok in>,<usd/Mtok out>; give two",
    )
    args = ap.parse_args(argv)
    if args.sample:
        return cmd_sample()
    if args.make_template:
        return cmd_template(args)
    if args.check_labels:
        return cmd_check_labels()
    if args.replay:
        return replay(args)
    if args.dry:
        doc = run_dry()
        _write(DRY_RESULT, doc)
        print_table(doc)
        return 0
    if args.generate:
        return live_generate(args)
    return live_judge(
        args
    )  # no flag, or --judge: it refuses without MILO_LIVE=1 (D126)


if __name__ == "__main__":
    raise SystemExit(main())
