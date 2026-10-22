"""M2 A/B evaluation of the advanced retrieval arms against the M1 dense baseline (protocol evals/protocols/m2-ab.md).

    python evals/retrieval/ab.py --dry                       offline: fake embedder, scripted model replies, committed fixture
    python evals/retrieval/ab.py --replay [--result F]       recompute every metric and paired CI from a stored result
    MILO_LIVE=1 python evals/retrieval/ab.py --max-cost 0.25 --reason "..." \\
        --chat-model <slug> --price-in <usd/Mtok> --price-out <usd/Mtok>     live: dev split, the integrator's batch

Arms: dense (baseline), rewrite (D10), selfquery (D11), rewrite_selfquery (D10 then D11), hybrid (D8: BM25 + dense,
RRF k = 60), rerank (D12: listwise over the dense top 10). Metrics per question, bootstrap CIs (2000, seed 0, D24)
and the paired bootstrap against dense reuse the M1 code in `run_retrieval.py`. Rows come from `evals/common.py`
(dev only). Live mode refuses without MILO_LIVE=1, `--max-cost` and `--reason` (SPEC D126, HARNESS rule 14); the
chat model goes through `milo.llm.cloud` and `llm/retry.py`. Model replies are stored in the rows (D56).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evals import common  # noqa: E402
from evals.retrieval import run_retrieval as rr  # noqa: E402
from milo.rag.bm25 import BM25Index  # noqa: E402
from milo.rag.chunk import Chunk, chunk_document, chunks_sha256  # noqa: E402
from milo.rag.embed import FakeEmbedder  # noqa: E402
from milo.rag.fusion import RRF_K, rrf  # noqa: E402
from milo.rag.ingest import INDEX_ROOT, kb_sha256  # noqa: E402
from milo.rag.rerank import ListwiseScorer, rerank_ids  # noqa: E402
from milo.rag.selfquery import (  # noqa: E402
    FilterExtractor,
    Vocabulary,
    self_query_rank,
)
from milo.rag.translate import Rewriter, rewrite_query  # noqa: E402
from milo.rag.vectors import VectorStore  # noqa: E402
from milo.types import ChatModel, ChatReply, Embeddings, ToolSpec  # noqa: E402

RESULTS = rr.RESULTS
AB_FIXTURE = ROOT / "evals" / "retrieval" / "fixtures" / "ab_dry_fixture.json"
DRY_RESULT = RESULTS / "ab_dry.json"
LIVE_RESULT = RESULTS / "ab_dev.json"
K, BOOT, SEED, METRICS = rr.K, rr.BOOT, rr.SEED, rr.METRICS
ARMS = ("dense", "rewrite", "selfquery", "rewrite_selfquery", "hybrid", "rerank")
CHALLENGERS = ARMS[1:]
RERANK_POOL = 10
LIVE_REFUSAL = (
    "refusing to run: a live A/B run calls the chat and embedding models on OpenRouter (SPEC D126) and needs "
    "MILO_LIVE=1, --max-cost, --reason, --chat-model, --price-in and --price-out; use --dry for the offline "
    "harness or --replay to recompute a stored result"
)


# ---------------------------------------------------------------- metering


class Meter:
    """Wraps a chat model: counts tokens and cost, so each call's usage lands in the row (D56)."""

    def __init__(self, chat: ChatModel | None = None) -> None:
        self.chat = chat
        self.price_usd_per_mtok = chat.price_usd_per_mtok if chat else (0.0, 0.0)
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost = 0.0

    def invoke(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec] | None = None
    ) -> ChatReply:
        assert self.chat is not None
        reply = self.chat.invoke(messages, tools)
        self.tokens_in += reply.usage.input_tokens
        self.tokens_out += reply.usage.output_tokens
        cost = getattr(self.chat, "last_cost_usd", None)
        if cost is None:
            pin, pout = self.price_usd_per_mtok
            cost = (
                reply.usage.input_tokens * pin + reply.usage.output_tokens * pout
            ) / 1e6
        self.cost += float(cost)
        return reply

    def snap(self) -> tuple[int, int, float]:
        return self.tokens_in, self.tokens_out, self.cost

    def since(self, before: tuple[int, int, float]) -> dict[str, Any]:
        return {
            "tokens_in": self.tokens_in - before[0],
            "tokens_out": self.tokens_out - before[1],
            "cost_usd": round(self.cost - before[2], 8),
        }


class Backends:
    """Everything model-dependent, behind callables: embedder, rewriter, filter extractor, reranker."""

    def __init__(
        self,
        embedder: Embeddings,
        rewriter: Rewriter,
        extractor: FilterExtractor,
        scorer: ListwiseScorer,
        meter: Meter,
    ) -> None:
        self.embedder, self.rewriter = embedder, rewriter
        self.extractor, self.scorer, self.meter = extractor, scorer, meter


# ---------------------------------------------------------------- arms


def _ids(hits: list[Any]) -> list[str]:
    return [h.chunk_id for h in hits]


def _arm(ids: list[str], relevant: set[str], **extra: Any) -> dict[str, Any]:
    return {
        "top5": ids[:K],
        "relevant_ranks": [i + 1 for i, c in enumerate(ids) if c in relevant],
        **extra,
    }


def run_question(
    q: dict[str, Any],
    qvec: list[float],
    store: VectorStore,
    chunks: list[Chunk],
    bm25: BM25Index,
    vocab: Vocabulary,
    be: Backends,
) -> dict[str, Any]:
    question = q["question"]
    answerable = bool(q["quotes"])
    relevant = rr.relevant_ids(chunks, q["quotes"]) if answerable else []
    rel = set(relevant)
    text_of = {c.chunk_id: c.text for c in chunks}
    calls: dict[str, dict[str, Any]] = {}

    def logged(name: str, fn: Callable[[], Any]) -> Any:
        before = be.meter.snap()
        out = fn()
        calls[name] = {"reply": "", **be.meter.since(before)}
        return out

    dense_hits = store.rank(qvec)
    dense = _ids(dense_hits)
    row: dict[str, Any] = {
        "qid": q["qid"],
        "type": q["type"],
        "question": question,
        "answerable": answerable,
        "gold_filters": q["filters"],
        "relevant": relevant,
        "dense_top1_score": round(dense_hits[0].score, 6) if dense_hits else 0.0,
    }
    arms: dict[str, dict[str, Any]] = {"dense": _arm(dense, rel)}

    rw_text, rw_raw = logged("rewrite", lambda: rewrite_query(question, be.rewriter))
    calls["rewrite"]["reply"] = rw_raw
    row["rewrite"] = rw_text
    rvec = be.embedder.embed([rw_text])[0]
    rw_hits = store.rank(rvec)
    arms["rewrite"] = _arm(_ids(rw_hits), rel)

    def selfquery(text: str, vec: list[float], name: str) -> dict[str, Any]:
        raw_box: list[str] = []

        def extract(t: str) -> str:
            raw_box.append(be.extractor(t))
            return raw_box[-1]

        hits, out = logged(
            name,
            lambda: self_query_rank(lambda f: store.rank(vec, f), text, extract, vocab),
        )
        calls[name]["reply"] = raw_box[0] if raw_box else ""
        return _arm(
            _ids(hits),
            rel,
            filters=out.filters,
            applied=out.applied,
            fallback=out.fallback,
        )

    arms["selfquery"] = selfquery(question, qvec, "selfquery")
    arms["rewrite_selfquery"] = selfquery(rw_text, rvec, "selfquery_rewritten")

    fused = [cid for cid, _ in rrf([dense, [c for c, _ in bm25.rank(question)]], RRF_K)]
    arms["hybrid"] = _arm(fused, rel)

    pool = [(cid, text_of[cid]) for cid in dense[:RERANK_POOL]]
    order, raw, ok = logged("rerank", lambda: rerank_ids(question, pool, be.scorer))
    calls["rerank"]["reply"] = raw
    arms["rerank"] = _arm(order + dense[RERANK_POOL:], rel, usable=ok)

    row["arms"] = arms
    row["calls"] = calls
    return row


# ---------------------------------------------------------------- statistics


def paired_ci(diffs: list[float], draws: list[list[int]]) -> list[float]:
    means = sorted(sum(diffs[i] for i in d) / len(d) for d in draws)
    return [round(means[int(0.025 * BOOT)], 4), round(means[int(0.975 * BOOT) - 1], 4)]


def paired_draws(n: int) -> list[list[int]]:
    rng = random.Random(SEED)
    return [[rng.randrange(n) for _ in range(n)] for _ in range(BOOT)]


def decide(
    paired: dict[str, Any], arm: str, verdicts: dict[str, str]
) -> dict[str, str]:
    nd, rc = paired["ndcg5"], paired["recall5"]
    lo, mean_d = nd["ci"][0], nd["mean_diff"]
    if lo > 0:
        verdict, why = (
            "adopt",
            f"paired nDCG@5 CI [{nd['ci'][0]}, {nd['ci'][1]}] above zero",
        )
    elif arm == "hybrid" and mean_d >= 0 and rc["mean_diff"] >= 0:
        verdict, why = (
            "no_harm_not_adopted",
            f"CI [{nd['ci'][0]}, {nd['ci'][1]}] includes zero; mean >= 0, recall@5 not lower",
        )
    else:
        verdict, why = (
            "not_adopted",
            f"paired nDCG@5 CI [{nd['ci'][0]}, {nd['ci'][1]}] does not lie above zero",
        )
    if arm == "rewrite_selfquery" and verdict == "adopt":
        if "adopt" not in (verdicts["rewrite"], verdicts["selfquery"]):
            verdict, why = (
                "not_adopted",
                "clears the bar, but neither rewrite nor selfquery does alone (protocol)",
            )
    return {"verdict": verdict, "rule": why}


def arm_notes(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    """Self-query, rewrite and rerank diagnostics from the stored rows."""
    out: dict[str, Any] = {}
    entries = [(r, r["arms"][arm]) for r in rows]
    if arm in ("selfquery", "rewrite_selfquery"):
        fb: dict[str, int] = {}
        for _, a in entries:
            if a["fallback"]:
                fb[a["fallback"]] = fb.get(a["fallback"], 0) + 1
        gold = [(r, a) for r, a in entries if r["gold_filters"]]

        def pred(a: dict[str, Any]) -> dict[str, Any]:
            return {
                k: v
                for k, v in (a["filters"] or {}).items()
                if k in ("section", "product")
            }

        out.update(
            applied=sum(1 for _, a in entries if a["applied"]),
            fallbacks=fb,
            filter_exact_on_gold_rows={"n": len(gold), "exact": sum(1 for r, a in gold if pred(a) == r["gold_filters"])},
            spurious_filter_on_unfiltered_gold_rows=sum(1 for r, a in entries if r["answerable"] and not r["gold_filters"] and pred(a)),
            filter_excluded_every_relevant=sum(1 for r, a in entries if a["applied"] and r["relevant"] and not a["relevant_ranks"]),
        )  # fmt: skip
    if arm in ("rewrite", "rewrite_selfquery"):
        out["rewrite_changed"] = sum(1 for r in rows if r["rewrite"] != r["question"])
    if arm == "rerank":
        out["unusable_replies"] = sum(1 for _, a in entries if not a["usable"])
    return out


def derive(rows: list[dict[str, Any]]) -> dict[str, Any]:
    for r in rows:
        if r["answerable"]:
            for a in r["arms"].values():
                a["metrics"] = rr.metrics_from_ranks(
                    a["relevant_ranks"], len(r["relevant"])
                )
    scored = [r for r in rows if r["answerable"]]
    draws = paired_draws(len(scored)) if scored else []
    summary: dict[str, Any] = {}
    types: dict[str, Any] = {}
    paired: dict[str, Any] = {}
    for arm in ARMS:
        items = [
            {"type": r["type"], "metrics": r["arms"][arm]["metrics"]} for r in scored
        ]
        summary[arm] = rr.summarise(items)
        types[arm] = rr.by_type(items)
        if arm == "dense" or not scored:
            continue
        paired[arm] = {
            m: (
                lambda d: {"mean_diff": round(rr.mean(d), 4), "ci": paired_ci(d, draws)}
            )(
                [
                    r["arms"][arm]["metrics"][m] - r["arms"]["dense"]["metrics"][m]
                    for r in scored
                ]
            )
            for m in METRICS
        }
    decisions: dict[str, Any] = {}
    verdicts: dict[str, str] = {}
    for arm in CHALLENGERS:
        if arm in paired:
            decisions[arm] = decide(paired[arm], arm, verdicts)
            verdicts[arm] = decisions[arm]["verdict"]
    usage = {
        "tokens_in": sum(c["tokens_in"] for r in rows for c in r["calls"].values()),
        "tokens_out": sum(c["tokens_out"] for r in rows for c in r["calls"].values()),
        "cost_usd": round(
            sum(c["cost_usd"] for r in rows for c in r["calls"].values()), 6
        ),
    }
    return {
        "summary": summary,
        "by_type": types,
        "paired_vs_dense": paired,
        "decisions": decisions,
        "arm_notes": {arm: arm_notes(rows, arm) for arm in CHALLENGERS} if rows else {},
        "usage": usage,
    }


# ---------------------------------------------------------------- inputs and backends


def scripted_backends(script: dict[str, Any]) -> Backends:
    """Dry backends: replies looked up in the committed fixture; an unknown input gets the neutral reply."""
    rewrites, filters, orders = script["rewrite"], script["selfquery"], script["rerank"]
    return Backends(
        FakeEmbedder(),
        lambda q: rewrites.get(q, q),
        lambda t: filters.get(t, "{}"),
        lambda q, texts: orders.get(q, ""),
        Meter(),
    )


def load_inputs(dry: bool) -> dict[str, Any]:
    if dry:
        fx = json.loads(AB_FIXTURE.read_text(encoding="utf-8"))
        base_path = AB_FIXTURE.parent / fx["base"]
        prep = rr.prepare(argparse.Namespace(dry=True))
        return {
            **prep,
            "script": fx["scripts"],
            "inputs": {"fixture": base_path, "ab_fixture": AB_FIXTURE},
        }
    return {**rr.prepare(argparse.Namespace(dry=False)), "script": None}


def make_index(
    docs: list[Any], embedder: Embeddings, model: str, dry: bool
) -> tuple[VectorStore, list[Chunk], int]:
    size, overlap = 800, 100
    if dry:
        return rr.build_store(docs, embedder, size, overlap, None)
    path = INDEX_ROOT / f"{model.replace('/', '_')}-{size}-{overlap}"
    chunks = [
        c
        for d in docs
        for c in chunk_document(d.doc_id, d.text, d.metadata, size, overlap)
    ]
    store = VectorStore(path) if path.exists() else None
    manifest = store.read_manifest() if store else None
    if store and manifest and manifest.get("chunk_sha256") == chunks_sha256(chunks):
        return store, chunks, int(manifest.get("dim", 0))
    store, chunks, dim = rr.build_store(docs, embedder, size, overlap, path)
    store.write_manifest({"model": model, "chunk_size": size, "overlap": overlap, "chunk_count": len(chunks), "dim": dim, "chunk_sha256": chunks_sha256(chunks), "kb_sha256": kb_sha256(rr.KB)})  # fmt: skip
    return store, chunks, dim


def run_arms(
    prep: dict[str, Any],
    be: Backends,
    model: str,
    dry: bool,
    max_cost: float | None,
) -> dict[str, Any]:
    store, chunks, dim = make_index(prep["docs"], be.embedder, model, dry)
    bm25 = BM25Index(chunks)
    vocab = Vocabulary.from_metadata(d.metadata for d in prep["docs"])
    queries = prep["queries"]
    qvecs = be.embedder.embed([q["question"] for q in queries])
    rows: list[dict[str, Any]] = []
    partial = False
    for q, qv in zip(queries, qvecs, strict=True):
        if max_cost is not None and be.meter.cost >= max_cost:
            partial = True  # the cap is checked between questions; one question's calls can overshoot it
            break
        rows.append(run_question(q, rr.f32(qv), store, chunks, bm25, vocab, be))
    return {
        "rows": rows,
        "chunks": chunks,
        "dim": dim,
        "partial": partial,
        "vocab": vocab,
    }


# ---------------------------------------------------------------- run


def run(args: argparse.Namespace) -> int:
    dry = bool(args.dry)
    be: Backends
    if dry:
        prep = load_inputs(True)
        be = scripted_backends(prep["script"])
        model = f"scripted-replies+{FakeEmbedder.model}"
        out, max_cost = DRY_RESULT, None
    else:
        missing = [
            n for n in ("max_cost", "reason", "chat_model", "price_in", "price_out")
            if getattr(args, n) in (None, "")
        ]  # fmt: skip
        if os.environ.get("MILO_LIVE") != "1" or missing:
            print(LIVE_REFUSAL, file=sys.stderr)
            return 2
        from milo.llm.cloud import OpenRouterChat
        from milo.rag.embed import live_embedder
        from milo.rag.selfquery import chat_extractor
        from milo.rag.rerank import chat_reranker
        from milo.rag.translate import chat_rewriter
        from milo.settings import Settings

        key = Settings.from_env().openrouter_api_key
        if not key:
            print(
                "refusing to run: OPENROUTER_API_KEY is not set in the environment",
                file=sys.stderr,
            )
            return 2
        prep = load_inputs(False)
        embedder = live_embedder()
        chat = OpenRouterChat(
            args.chat_model,
            key,
            (args.price_in, args.price_out),
            purpose="m2-ab",
            temperature=0.0,
        )
        meter = Meter(chat)
        vocab0 = Vocabulary.from_metadata(d.metadata for d in prep["docs"])
        be = Backends(
            embedder, chat_rewriter(meter),
            chat_extractor(meter, sorted(vocab0.sections), sorted(vocab0.products)),
            chat_reranker(meter), meter,
        )  # fmt: skip
        model = f"chat={args.chat_model}; embedding={embedder.model}"
        out, max_cost = LIVE_RESULT, float(args.max_cost)
    run_out = run_arms(
        prep, be, str(getattr(be.embedder, "model", model)), dry, max_cost
    )
    rows, chunks = run_out["rows"], run_out["chunks"]
    derived = derive(rows)
    inputs: dict[str, Path | str] = {**prep["inputs"], "script": Path(__file__)}
    for mod in ("translate", "selfquery", "bm25", "fusion", "rerank"):
        inputs[f"src/milo/rag/{mod}.py"] = ROOT / "src" / "milo" / "rag" / f"{mod}.py"
    cmd = (
        "python evals/retrieval/ab.py --dry" if dry
        else f"MILO_LIVE=1 python evals/retrieval/ab.py --max-cost {args.max_cost} --chat-model {args.chat_model}"
    )  # fmt: skip
    result: dict[str, Any] = {
        **common.provenance(model=model, seed=SEED, temperature=0.0, command=cmd, inputs=inputs),
        "temperature": None if dry else 0.0,
        "temperature_note": "scripted replies, no model" if dry else "chat calls at temperature 0; embeddings have none",
        "dry": dry,
        "split": "dev",
        "k": K,
        "bootstrap": BOOT,
        "rrf_k": RRF_K,
        "rerank_pool": RERANK_POOL,
        "arms": list(ARMS),
        "baseline": "dense",
        "chunk_size": 800,
        "overlap": 100,
        "chunk_count": len(chunks),
        "chunk_sha256": chunks_sha256(chunks),
        "embedding_dim": run_out["dim"],
        "embedding_requests": getattr(be.embedder, "calls", 0),
        "partial": run_out["partial"],
        "completed_questions": len(rows),
        "total_cost_usd": 0.0 if dry else derived["usage"]["cost_usd"],
        "cost_note": "dry run: no call" if dry else "chat cost summed from the provider's usage field per call; embedding cost is not returned by cloud.py (docs/requests/m1.md) and is below 0.001 USD",
        "spend_reason": "none (scripted replies, fake embedder)" if dry else args.reason,
        **derived,
        "rows": rows,
    }  # fmt: skip
    RESULTS.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {out.relative_to(ROOT)} ({len(rows)} rows, {len(chunks)} chunks)"
        + (" PARTIAL" if run_out["partial"] else "")
    )
    print_table(result)
    return 0


def print_table(res: dict[str, Any]) -> None:
    print(
        f"{'arm':<19}{'nDCG@5':>8}  {'95% CI':<16}{'d vs dense':>11}  {'paired CI':<16}decision"
    )
    for arm in ARMS:
        s = res["summary"][arm]["ndcg5"]
        p = res["paired_vs_dense"].get(arm)
        d = res["decisions"].get(arm, {})
        print(f"{arm:<19}{s['mean']!s:>8}  {s['ci']!s:<16}{(p['ndcg5']['mean_diff'] if p else '-')!s:>11}  {(p['ndcg5']['ci'] if p else '-')!s:<16}{d.get('verdict', 'baseline')}")  # fmt: skip


# ---------------------------------------------------------------- replay


def replay_one(path: Path) -> bool:
    stored = json.loads(path.read_text(encoding="utf-8"))
    rows = copy.deepcopy(stored["rows"])
    derived = derive(rows)
    bad: list[str] = []
    for key, val in derived.items():
        rr._same(stored[key], val, 0.0, key, bad)
    rr._same(stored["rows"], rows, 0.0, "rows", bad)
    note = "recomputed from rows"
    if stored["dry"] and not bad:
        prep = load_inputs(True)
        fresh = run_arms(prep, scripted_backends(prep["script"]), "dry", True, None)[
            "rows"
        ]
        derive(fresh)
        rr._same(stored["rows"], fresh, 0.0, "regenerated", bad)
        note += "; rows regenerated from the fixture"
    print(f"{'MATCH' if not bad else 'MISMATCH'} {rr._show(path)}: {note}")
    for line in bad[:20]:
        print("  ", line)
    return not bad


def replay(args: argparse.Namespace) -> int:
    paths = (
        [Path(args.result)]
        if args.result
        else [p for p in (LIVE_RESULT, DRY_RESULT) if p.exists()]
    )
    if not paths:
        print(
            "no stored result to replay (evals/results/retrieval/ab_*.json)",
            file=sys.stderr,
        )
        return 1
    return 0 if all([replay_one(p) for p in paths]) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry",
        action="store_true",
        help="scripted replies and a fake embedder on the committed fixture",
    )
    mode.add_argument(
        "--replay", action="store_true", help="recompute from a stored result"
    )
    ap.add_argument("--result", help="result file to replay")
    ap.add_argument(
        "--max-cost", type=float, help="live: stop after this many USD of chat spend"
    )
    ap.add_argument(
        "--reason", help="live: why the spend is needed (recorded in the result)"
    )
    ap.add_argument(
        "--chat-model", help="live: OpenRouter slug of the dev chat model (E02, D3)"
    )
    ap.add_argument(
        "--price-in", type=float, help="live: listed USD per million input tokens"
    )
    ap.add_argument(
        "--price-out", type=float, help="live: listed USD per million output tokens"
    )
    args = ap.parse_args(argv)
    if args.replay:
        return replay(args)
    return run(args)  # no flag means live: it refuses without MILO_LIVE=1


if __name__ == "__main__":
    raise SystemExit(main())
