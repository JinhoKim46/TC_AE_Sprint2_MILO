"""M1 dense retrieval evaluation on the dev split (SPEC sections 4 and 10, D5, D9, D24, D56, D126).

    MILO_LIVE=1 python evals/retrieval/run_retrieval.py            live: embeds the KB and the dev questions
    python evals/retrieval/run_retrieval.py --dry                  offline: fake embedder on a committed fixture
    python evals/retrieval/run_retrieval.py --replay [--result F]  recompute every metric from a stored result

Live mode refuses without MILO_LIVE=1 (the integrator runs it once under evals/protocols/e01-rerun.md, HARNESS
rule 14). It reads the key from the process environment only. Rows come from `evals/common.py`, which returns
dev rows; the test split is never read here. Metrics per question (binary relevance: a chunk is relevant when
it holds a gold quote after whitespace collapse and lower-casing, in the gold document): recall@5, nDCG@5, MRR
and AP over the full ranking, precision@5. Means carry a 95 percent bootstrap CI (2000 resamples, seed 0, D24).
Unanswerable rows give the top-1 score from which `min_score` is calibrated (SPEC 4, D25).

Files, next to each other in evals/results/retrieval/:
    retrieval_dev.json + retrieval_dev_query_embeddings.json    live
    retrieval_dry.json + retrieval_dry_query_embeddings.json    dry (synthetic fixture; never a MILO result)
Replay recomputes the metrics from the stored rows and must match exactly. If the embeddings file and an index
(the dry fixture, or the live index under data/index/) are present, it also re-ranks the stored query vectors.
"""

from __future__ import annotations

import argparse
import array
import base64
import json
import math
import os
import random
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evals import common  # noqa: E402
from milo.rag.chunk import (  # noqa: E402
    DEFAULT_OVERLAP,
    DEFAULT_SIZE,
    Chunk,
    chunk_document,
    chunks_sha256,
)
from milo.rag.embed import FakeEmbedder  # noqa: E402
from milo.rag.ingest import INDEX_ROOT, kb_sha256, load_kb  # noqa: E402
from milo.rag.vectors import VectorStore  # noqa: E402
from milo.types import Document, Embeddings  # noqa: E402

RESULTS = ROOT / "evals" / "results" / "retrieval"
FIXTURE = ROOT / "evals" / "retrieval" / "fixtures" / "retrieval_dry_fixture.json"
KB = ROOT / "data" / "kb"
LIVE_RESULT = RESULTS / "retrieval_dev.json"
DRY_RESULT = RESULTS / "retrieval_dry.json"
K = 5
BOOT, SEED = 2000, 0
METRICS = ("recall5", "ndcg5", "mrr", "p5", "ap")
TOL = 0.005  # live rerun tolerance (HARNESS 7.7)
LIVE_REFUSAL = (
    "refusing to run: a live retrieval run embeds the KB and the dev questions on OpenRouter (SPEC D126) and needs "
    "MILO_LIVE=1; use --dry for the offline harness or --replay to recompute a stored result"
)


# ---------------------------------------------------------------- metrics


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def metrics_from_ranks(ranks: list[int], n_rel: int) -> dict[str, Any]:
    """`ranks`: 1-based ranks of the relevant chunks in the full ranking, ascending."""
    top = [r for r in ranks if r <= K]
    dcg = sum(1 / math.log2(r + 1) for r in top)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(n_rel, K)))
    return {
        "recall5": 1.0 if top else 0.0,
        "ndcg5": dcg / idcg if idcg else 0.0,
        "mrr": 1 / ranks[0] if ranks else 0.0,
        "p5": len(top) / K,
        "ap": sum((j + 1) / r for j, r in enumerate(ranks)) / n_rel if n_rel else 0.0,
        "first_relevant_rank": ranks[0] if ranks else None,
    }


def mean(v: list[float]) -> float:
    return sum(v) / len(v)


def boot_ci(vals: list[float]) -> list[float]:
    rng = random.Random(SEED)
    n = len(vals)
    means = sorted(
        mean([vals[rng.randrange(n)] for _ in range(n)]) for _ in range(BOOT)
    )
    return [round(means[int(0.025 * BOOT)], 4), round(means[int(0.975 * BOOT) - 1], 4)]


def summarise(rows: list[dict[str, Any]], key: str = "metrics") -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(rows)}
    for m in METRICS:
        vals = [r[key][m] for r in rows]
        out[m] = (
            {"mean": round(mean(vals), 4), "ci": boot_ci(vals)}
            if vals
            else {"mean": None, "ci": None}
        )
    return out


def by_type(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        t: summarise([r for r in rows if r["type"] == t])
        for t in sorted({r["type"] for r in rows})
    }


def weakest(types: dict[str, Any]) -> dict[str, Any] | None:
    scored = {
        t: s["ndcg5"]["mean"]
        for t, s in types.items()
        if s["ndcg5"]["mean"] is not None
    }
    if not scored:
        return None
    t = min(scored, key=lambda x: (scored[x], x))
    return {"type": t, "ndcg5": scored[t]}


def calibrate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick `min_score` from top-1 scores: answerable rows should pass, unanswerable rows should abstain."""
    ans = [r["top1_score"] for r in rows if r["answerable"] and r["relevant"]]
    una = [r["top1_score"] for r in rows if not r["answerable"]]
    out: dict[str, Any] = {
        "answerable_top1_min": min(ans) if ans else None,
        "unanswerable_top1_max": max(una) if una else None,
        "unanswerable_n": len(una),
        "fixed_030": _rates(ans, una, 0.30),
    }
    if not ans or not una:
        return {**out, "chosen": None, "at_chosen": None, "separable": None}
    pts = sorted(set(ans + una))
    cands = [
        round(math.floor((a + b) / 2 * 100) / 100, 2)
        for a, b in zip(pts, pts[1:], strict=False)
    ]
    cands = sorted(set(cands)) or [round(pts[0], 2)]

    def balanced(t: float) -> float:
        r = _rates(ans, una, t)
        return (r["answered_rate"] + r["abstain_rate"]) / 2

    best = max(balanced(t) for t in cands)
    chosen = min(
        t for t in cands if balanced(t) == best
    )  # the lowest threshold: fewest false abstentions
    return {
        **out,
        "chosen": chosen,
        "at_chosen": _rates(ans, una, chosen),
        "separable": min(ans) > max(una),
    }


def _rates(ans: list[float], una: list[float], t: float) -> dict[str, Any]:
    return {
        "min_score": t,
        "answered_rate": round(sum(a >= t for a in ans) / len(ans), 4) if ans else None,
        "abstain_rate": round(sum(u < t for u in una) / len(una), 4) if una else None,
    }


def derive(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything computed from rows. Replay calls this on the stored rows and compares."""
    for r in rows:
        if r["answerable"]:
            r["metrics"] = metrics_from_ranks(r["relevant_ranks"], len(r["relevant"]))
            if r["filters"]:
                f = r["filtered"]
                f["metrics"] = metrics_from_ranks(
                    f["relevant_ranks"], len(r["relevant"])
                )
    scored = [r for r in rows if r["answerable"]]
    with_chunk = [r for r in scored if r["relevant"]]
    filt = [r for r in scored if r["filters"]]
    types = by_type(scored)
    return {
        "summary": summarise(scored),
        "summary_excluding_no_chunk": summarise(with_chunk),
        "by_type": types,
        "weakest_type": weakest(types),
        "filtered": {
            "n": len(filt),
            "unfiltered_summary": summarise(filt),
            "filtered_summary": summarise(
                [{"metrics": r["filtered"]["metrics"]} for r in filt]  # type: ignore[misc]
            ),
        },
        "calibration": calibrate(rows),
    }


# ---------------------------------------------------------------- ranking


def f32(vec: list[float]) -> list[float]:
    return list(array.array("f", vec))


def encode_vecs(vecs: dict[str, list[float]]) -> dict[str, str]:
    return {
        q: base64.b64encode(array.array("f", v).tobytes()).decode()
        for q, v in vecs.items()
    }


def decode_vecs(enc: dict[str, str]) -> dict[str, list[float]]:
    out = {}
    for q, s in enc.items():
        a = array.array("f")
        a.frombytes(base64.b64decode(s))
        out[q] = list(a)
    return out


def relevant_ids(chunks: list[Chunk], quotes: list[dict[str, str]]) -> list[str]:
    rel = set()
    for ev in quotes:
        q = norm(ev["quote"])
        rel |= {
            c.chunk_id for c in chunks if c.doc_id == ev["doc_id"] and q in norm(c.text)
        }
    return sorted(rel)


def rank_rows(
    store: VectorStore,
    chunks: list[Chunk],
    queries: list[dict[str, Any]],
    qvecs: dict[str, list[float]],
) -> list[dict[str, Any]]:
    rows = []
    for q in queries:
        qv = qvecs[q["qid"]]
        answerable = bool(q["quotes"])
        full = store.rank(qv)
        relevant = relevant_ids(chunks, q["quotes"]) if answerable else []
        row: dict[str, Any] = {
            "qid": q["qid"],
            "type": q["type"],
            "question": q["question"],
            "answerable": answerable,
            "filters": q["filters"],
            "relevant": relevant,
            "top5": [h.chunk_id for h in full[:K]],
            "top5_scores": [round(h.score, 6) for h in full[:K]],
            "top1_score": round(full[0].score, 6) if full else 0.0,
            "relevant_ranks": [
                i + 1 for i, h in enumerate(full) if h.chunk_id in relevant
            ],
        }
        if q["filters"]:
            ff = store.rank(qv, q["filters"])
            row["filtered"] = {
                "top5": [h.chunk_id for h in ff[:K]],
                "top5_scores": [round(h.score, 6) for h in ff[:K]],
                "relevant_ranks": [
                    i + 1 for i, h in enumerate(ff) if h.chunk_id in relevant
                ],
            }
        rows.append(row)
    return rows


def build_store(
    docs: list[Document],
    embedder: Embeddings,
    size: int,
    overlap: int,
    path: Path | None,
) -> tuple[VectorStore, list[Chunk], int]:
    chunks = [
        c
        for d in docs
        for c in chunk_document(d.doc_id, d.text, d.metadata, size, overlap)
    ]
    vectors: list[list[float]] = []
    for i in range(0, len(chunks), 64):
        vectors += embedder.embed([c.text for c in chunks[i : i + 64]])
    store = VectorStore(path)
    store.add(chunks, vectors)
    return store, chunks, len(vectors[0]) if vectors else 0


def gold_queries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "qid": r["qid"],
            "type": r["type"],
            "question": r["question"],
            "quotes": [
                {"doc_id": e["doc_id"], "quote": e["quote"]} for e in r["gold_evidence"]
            ],
            "filters": r.get("filters") or None,
        }
        for r in rows
    ]


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    """Inputs of a run: documents, queries and the files hashed into `inputs`."""
    if args.dry:
        fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
        docs = [Document(d["doc_id"], d["text"], d["metadata"]) for d in fx["docs"]]
        return {"docs": docs, "queries": fx["queries"], "inputs": {"fixture": FIXTURE}}
    dev = common.load_gold("dev")  # dev rows only (HARNESS rule 6)
    inputs: dict[str, Path | str] = {"gold": common.GOLD_PATH}
    for md in sorted(KB.rglob("*.md")):
        inputs[f"kb/{md.relative_to(KB).as_posix()}"] = md
    return {"docs": load_kb(KB), "queries": gold_queries(dev), "inputs": inputs}


# ---------------------------------------------------------------- run


def run(args: argparse.Namespace) -> int:
    size, overlap = (int(x) for x in args.chunk.split("/"))
    if not args.dry and os.environ.get("MILO_LIVE") != "1":
        print(LIVE_REFUSAL, file=sys.stderr)
        return 2
    prep = prepare(args)
    embedder: Any
    calls = 0
    index_path: Path | None = None
    if args.dry:
        embedder, model = FakeEmbedder(), FakeEmbedder.model
        out, emb_out = DRY_RESULT, RESULTS / "retrieval_dry_query_embeddings.json"
    else:
        if os.environ.get("MILO_LIVE") != "1":
            print(LIVE_REFUSAL, file=sys.stderr)
            return 2
        from milo.rag.embed import live_embedder

        try:
            embedder = live_embedder()
        except PermissionError as exc:
            print(f"refusing to run: {exc}", file=sys.stderr)
            return 2
        model = str(embedder.model)
        out, emb_out = LIVE_RESULT, RESULTS / "retrieval_dev_query_embeddings.json"
        index_path = INDEX_ROOT / f"{model.replace('/', '_')}-{size}-{overlap}"
    store, chunks, dim = build_store(prep["docs"], embedder, size, overlap, index_path)
    if index_path is not None:
        store.write_manifest(
            {
                "model": model, "chunk_size": size, "overlap": overlap, "chunk_count": len(chunks), "dim": dim,
                "chunk_sha256": chunks_sha256(chunks), "kb_sha256": kb_sha256(KB),
            }
        )  # fmt: skip
    queries = prep["queries"]
    qlist = embedder.embed([q["question"] for q in queries])
    qvecs = {
        q["qid"]: f32(v) for q, v in zip(queries, qlist, strict=True)
    }  # float32, as stored
    rows = rank_rows(store, chunks, queries, qvecs)
    derived = derive(rows)
    inputs = {**prep["inputs"], "script": Path(__file__)}
    result: dict[str, Any] = {
        **common.provenance(
            model=model, seed=SEED, temperature=0.0,
            command=("python evals/retrieval/run_retrieval.py --dry" if args.dry
                     else "MILO_LIVE=1 python evals/retrieval/run_retrieval.py"),
            inputs=inputs,
        ),
        "temperature": None,
        "temperature_note": "embeddings have no temperature",
        "dry": bool(args.dry),
        "split": "dev",
        "k": K,
        "bootstrap": BOOT,
        "query_prefix": "none",
        "chunk_size": size,
        "overlap": overlap,
        "chunk_count": len(chunks),
        "chunk_sha256": chunks_sha256(chunks),
        "embedding_dim": dim,
        "embedding_requests": getattr(embedder, "calls", 0) + calls,
        "total_cost_usd": None if not args.dry else 0.0,
        "cost_note": "dry run: no call" if args.dry else "pending: cloud.py does not return the usage field of an embedding call (docs/requests/m1.md); the integrator records the spend from the OpenRouter ledger",
        "spend_reason": "none (fake embedder)" if args.dry else "dense retrieval baseline needs the product embedding model's vectors for the KB and the dev questions (D126)",
        "queries_without_chunk": [r["qid"] for r in rows if r["answerable"] and not r["relevant"]],
        **derived,
        "rows": rows,
    }  # fmt: skip
    RESULTS.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    emb_out.write_text(
        json.dumps(
            {
                "model": model,
                "dim": dim,
                "encoding": "float32-base64",
                "queries": encode_vecs(qvecs),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    s = derived["summary"]
    print(f"wrote {out.relative_to(ROOT)} ({len(rows)} rows, {len(chunks)} chunks)")
    print(
        f"recall@5 {s['recall5']['mean']}  nDCG@5 {s['ndcg5']['mean']}  MRR {s['mrr']['mean']}  weakest {derived['weakest_type']}"
    )
    return 0


# ---------------------------------------------------------------- replay


def _same(a: Any, b: Any, tol: float, path: str, bad: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                bad.append(f"{path}.{k}: missing")
            else:
                _same(a[k], b[k], tol, f"{path}.{k}", bad)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            bad.append(f"{path}: length {len(a)} != {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b, strict=True)):
                _same(x, y, tol, f"{path}[{i}]", bad)
    elif isinstance(a, float) and isinstance(b, float) and tol:
        if abs(a - b) > tol:
            bad.append(f"{path}: {a} vs {b}")
    elif a != b:
        bad.append(f"{path}: {a!r} != {b!r}")


def _show(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def replay_one(path: Path) -> bool:
    stored = json.loads(path.read_text(encoding="utf-8"))
    rows = json.loads(json.dumps(stored["rows"]))
    derived = derive(rows)
    bad: list[str] = []
    for key, val in derived.items():
        _same(stored[key], val, 0.0, key, bad)
    _same(stored["rows"], rows, 0.0, "rows", bad)
    note = "recomputed from rows"
    emb_path = path.with_name(path.stem + "_query_embeddings.json")
    if emb_path.exists() and not bad:
        note += "; " + rerank_check(stored, emb_path, bad)
    print(f"{'MATCH' if not bad else 'MISMATCH'} {_show(path)}: {note}")
    for line in bad[:20]:
        print("  ", line)
    return not bad


def rerank_check(stored: dict[str, Any], emb_path: Path, bad: list[str]) -> str:
    """Re-rank the stored query vectors against the index; exact for the dry fixture, within TOL for live."""
    emb = json.loads(emb_path.read_text(encoding="utf-8"))
    qvecs = decode_vecs(emb["queries"])
    size, overlap = stored["chunk_size"], stored["overlap"]
    if stored["dry"]:
        args = argparse.Namespace(dry=True)
        prep = prepare(args)
        store, chunks, _ = build_store(
            prep["docs"], FakeEmbedder(), size, overlap, None
        )
        tol = 1e-6
    else:
        idx = INDEX_ROOT / f"{str(stored['model']).replace('/', '_')}-{size}-{overlap}"
        manifest = VectorStore(idx).read_manifest() if idx.exists() else None
        if not manifest or manifest.get("chunk_sha256") != stored["chunk_sha256"]:
            return "index not on disk (data/index/), query-embedding re-rank skipped"
        store = VectorStore(idx)
        chunks = [
            c
            for d in load_kb(KB)
            for c in chunk_document(d.doc_id, d.text, d.metadata, size, overlap)
        ]
        tol = TOL
    fresh = rank_rows_from_stored(store, chunks, stored["rows"], qvecs)
    again = derive(fresh)
    for key in ("summary", "calibration"):
        _same(stored[key], again[key], tol, f"rerank.{key}", bad)
    return f"re-ranked {len(fresh)} stored query vectors (tolerance {tol})"


def rank_rows_from_stored(
    store: VectorStore,
    chunks: list[Chunk],
    stored_rows: list[dict[str, Any]],
    qvecs: dict[str, list[float]],
) -> list[dict[str, Any]]:
    """Rank again with the stored relevant ids (the quotes are not kept in a result file)."""
    rows = []
    for r in stored_rows:
        full = store.rank(qvecs[r["qid"]])
        rel = set(r["relevant"])
        row = {**r, "top5": [h.chunk_id for h in full[:K]], "top5_scores": [round(h.score, 6) for h in full[:K]],
               "top1_score": round(full[0].score, 6) if full else 0.0,
               "relevant_ranks": [i + 1 for i, h in enumerate(full) if h.chunk_id in rel]}  # fmt: skip
        if r["filters"]:
            ff = store.rank(qvecs[r["qid"]], r["filters"])
            row["filtered"] = {"top5": [h.chunk_id for h in ff[:K]], "top5_scores": [round(h.score, 6) for h in ff[:K]],
                               "relevant_ranks": [i + 1 for i, h in enumerate(ff) if h.chunk_id in rel]}  # fmt: skip
        rows.append(row)
    return rows


def replay(args: argparse.Namespace) -> int:
    paths = (
        [Path(args.result)]
        if args.result
        else [p for p in (LIVE_RESULT, DRY_RESULT) if p.exists()]
    )
    if not paths:
        print(
            "no stored result to replay (evals/results/retrieval/retrieval_*.json)",
            file=sys.stderr,
        )
        return 1
    ok = [replay_one(p) for p in paths]
    return 0 if all(ok) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry", action="store_true", help="fake embedder on the committed fixture"
    )
    mode.add_argument(
        "--replay", action="store_true", help="recompute from a stored result"
    )
    ap.add_argument("--result", help="result file to replay")
    ap.add_argument(
        "--chunk",
        default=f"{DEFAULT_SIZE}/{DEFAULT_OVERLAP}",
        help="chunk size/overlap",
    )
    args = ap.parse_args(argv)
    if args.replay:
        return replay(args)
    return run(args)  # no flag means live: it refuses without MILO_LIVE=1


if __name__ == "__main__":
    raise SystemExit(main())
