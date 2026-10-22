"""Embedding comparison on the dev split (SPEC D5, D24). Uses OpenRouter embeddings through `milo.llm.cloud`.

The arms (the five models in order, the reference, the chunk settings) are read from
`evals/protocols/e01-rerun.md`; this file holds only the fallback price per model, used when a response carries no
cost. Run from the repo root; it spends OpenRouter budget and refuses without MILO_LIVE=1 (SPEC D126):
    MILO_LIVE=1 python evals/retrieval/compare_embeddings.py

Metrics per question (binary relevance; a chunk is relevant when it contains the gold quote):
    recall@5, nDCG@5, MRR (full ranking), precision@5, average precision (full ranking).
Comparisons: paired bootstrap of the per-question difference between each model and the chosen
reference model (2000 resamples, seed 0). Chunk configurations: 800/100, 400/50, 1500/150 characters.
Never reads the test split. Writes evals/results/retrieval/embedding_dev_v3.json (the file e01-rerun.md names).

    MILO_LIVE=1 python evals/retrieval/compare_embeddings.py [--max-cost 0.05]   live (stops between models past the cap)
    python evals/retrieval/compare_embeddings.py --replay [--result F]          recompute summaries and paired CIs from rows
"""
import argparse, hashlib, json, math, os, random, re, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from evals import common  # noqa: E402
from evals.common import load_gold  # the only reader of the gold file (SPEC 10.1)
OUT = Path(os.environ.get("EMBED_OUT", REPO / "evals" / "results" / "retrieval"))
PROTOCOL = REPO / "evals" / "protocols" / "e01-rerun.md"
RESULT_NAME = "embedding_dev_v3.json"
DEFAULT_CAP_USD = 0.05  # e01-rerun.md, measured-call budget
SPEND_REASON = "candidate embedding vectors needed for the comparison (D126)"
METRICS = ("recall5", "ndcg5", "mrr", "p5", "ap")
# Fallback USD per token when a response has no cost field (the listed prices of E01/E01b).
LISTED_PRICE = {
    "google/gemini-embedding-2": 0.0000002,
    "openai/text-embedding-3-large": 0.00000013,
    "mistralai/mistral-embed-2312": 0.0000001,
    "qwen/qwen3-embedding-8b": 0.00000001,
    "openai/text-embedding-3-small": 0.00000002,
}


def protocol_arms(text):
    """(models in table order, reference slug, chunk settings) from the protocol text."""
    models = re.findall(r"^\| `([a-z0-9-]+/[A-Za-z0-9._-]+)` \| (reference|candidate)", text, re.M)
    if not models:
        raise SystemExit(f"no model table found in {PROTOCOL}")
    refs = [m for m, role in models if role == "reference"]
    if len(refs) != 1:
        raise SystemExit("the protocol must name exactly one reference model")
    configs = []
    m = re.search(r"Chunk settings.*?(\d+) characters with (\d+) overlap.*?,\s*(\d+)/(\d+),\s*(\d+)/(\d+)", text, re.S)
    if not m:
        raise SystemExit("no chunk settings found in the protocol")
    a1, a2, b1, b2, c1, c2 = (int(x) for x in m.groups())
    configs = [(a1, a2), (b1, b2), (c1, c2)]
    missing = [slug for slug, _ in models if slug not in LISTED_PRICE]
    if missing:
        raise SystemExit(f"no fallback price for {missing}; add it to LISTED_PRICE")
    return [(slug, LISTED_PRICE[slug]) for slug, _ in models], refs[0], configs


MODELS, REFERENCE, CHUNK_CONFIGS = protocol_arms(PROTOCOL.read_text(encoding="utf-8"))
K = 5
BOOT, SEED = 2000, 0


def norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def chunks_from_kb(size, overlap):
    out = []
    for md in sorted((REPO / "data" / "kb").rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        doc_id = md.relative_to(REPO / "data" / "kb").with_suffix("").as_posix()
        body = text.split("---", 2)[2] if text.startswith("---") else text
        sections, cur = [], []
        for line in body.splitlines():
            if line.startswith("#") and cur:
                sections.append("\n".join(cur)); cur = []
            cur.append(line)
        if cur:
            sections.append("\n".join(cur))
        for sec in sections:
            sec = sec.strip()
            start = 0
            while start < len(sec):
                out.append((doc_id, sec[start:start + size]))
                if start + size >= len(sec):
                    break
                start += size - overlap
    return [{"chunk_id": f"{d}#{i}", "doc_id": d, "text": t} for i, (d, t) in enumerate(out)]


def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def per_question_metrics(ranked_ids, relevant):
    """ranked_ids: full ranking of chunk ids. relevant: set of relevant chunk ids."""
    top = ranked_ids[:K]
    hits = [i for i, c in enumerate(ranked_ids) if c in relevant]
    recall = 1.0 if relevant & set(top) else 0.0
    dcg = sum(1 / math.log2(i + 2) for i, c in enumerate(top) if c in relevant)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), K)))
    ndcg = dcg / idcg if idcg else 0.0
    mrr = 1 / (hits[0] + 1) if hits else 0.0
    prec = len(relevant & set(top)) / K
    precisions = [(j + 1) / (pos + 1) for j, pos in enumerate(hits)]
    ap = sum(precisions) / len(relevant) if relevant else 0.0
    first_rank = hits[0] + 1 if hits else None
    return {"recall5": recall, "ndcg5": ndcg, "mrr": mrr, "p5": prec, "ap": ap,
            "first_relevant_rank": first_rank, "top5": top}


def mean(v):
    return sum(v) / len(v)


def boot_ci(vals):
    rng = random.Random(SEED)
    n = len(vals)
    means = sorted(mean([vals[rng.randrange(n)] for _ in range(n)]) for _ in range(BOOT))
    return [round(means[int(0.025 * BOOT)], 4), round(means[int(0.975 * BOOT) - 1], 4)]


def paired_diff(a, b):
    diffs = [x - y for x, y in zip(a, b)]
    return {"mean_diff": round(mean(diffs), 4), "ci": boot_ci(diffs)}


def summarise(rows, exclude=()):
    rows = [r for r in rows if r["qid"] not in set(exclude)]
    return {key: {"mean": round(mean([r[key] for r in rows]), 4), "ci": boot_ci([r[key] for r in rows])}
            for key in METRICS}


def paired(cfg_models, reference, exclude=()):
    out = {}
    ref_rows = [r for r in cfg_models[reference]["rows"] if r["qid"] not in set(exclude)]
    for model, block in cfg_models.items():
        if model == reference:
            continue
        rows = [r for r in block["rows"] if r["qid"] not in set(exclude)]
        out[model] = {key: paired_diff([r[key] for r in rows], [r[key] for r in ref_rows]) for key in METRICS}
    return out


def replay(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = []
    for cfg in data["configs"]:
        tag = f"{cfg['chunk_size']}/{cfg['overlap']}"
        excl = cfg["queries_without_chunk"]
        for model, block in cfg["models"].items():
            s = summarise(block["rows"])
            for key in METRICS:
                if s[key] != block["summary"][key]:
                    problems.append(f"{tag} {model} {key}")
            if excl and summarise(block["rows"], excl) != block.get("summary_excluding_no_chunk"):
                problems.append(f"{tag} {model} summary_excluding_no_chunk")
        ref = data["reference"]
        if paired(cfg["models"], ref) != json.loads(json.dumps(cfg["paired_vs_reference"])):
            problems.append(f"{tag} paired_vs_reference")
        if excl and paired(cfg["models"], ref, excl) != json.loads(json.dumps(cfg.get("paired_excluding_no_chunk"))):
            problems.append(f"{tag} paired_excluding_no_chunk")
    if problems:
        print("replay MISMATCH: " + "; ".join(problems), file=sys.stderr)
        return 1
    print(f"replay OK for {path}: {len(data['configs'])} chunk settings, reference {data['reference']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", action="store_true", help="recompute from a stored result (no network)")
    ap.add_argument("--result", default=str(OUT / RESULT_NAME), help="result file to replay")
    ap.add_argument("--max-cost", type=float, default=DEFAULT_CAP_USD, help="live: stop between models past this USD")
    args = ap.parse_args(argv)
    if args.replay:
        return replay(args.result)
    from milo.rag.embed import live_embedder  # the only client path: milo.llm.cloud (HARNESS rule 1)

    if os.environ.get("MILO_LIVE") != "1":
        raise SystemExit("refusing to run: embedding calls spend OpenRouter budget (SPEC D126) and need MILO_LIVE=1")
    dev = [g for g in load_gold("dev") if g["gold_evidence"]]
    results = {"configs": []}
    total_cost = 0.0
    for size, overlap in CHUNK_CONFIGS:
        chunks = chunks_from_kb(size, overlap)
        queries = []
        for g in dev:
            rel = set()
            for ev in g["gold_evidence"]:
                q = norm(ev["quote"])
                for c in chunks:
                    if c["doc_id"] == ev["doc_id"] and q in norm(c["text"]):
                        rel.add(c["chunk_id"])
            queries.append({"qid": g["qid"], "type": g["type"], "question": g["question"], "relevant": sorted(rel)})
        no_match = [q["qid"] for q in queries if not q["relevant"]]
        cfg = {"chunk_size": size, "overlap": overlap, "chunk_count": len(chunks),
               "query_count": len(queries), "queries_without_chunk": no_match,
               "chunk_sha256": hashlib.sha256(json.dumps(chunks, sort_keys=True).encode()).hexdigest(),
               "models": {}, "paired_vs_reference": {}}
        per_model = {}
        for model, price in MODELS:
            if total_cost >= args.max_cost:
                raise SystemExit(f"cost cap reached: {total_cost:.5f} USD spent, cap {args.max_cost}; no result written")
            try:
                emb = live_embedder(model, price_usd_per_mtok=price * 1_000_000)  # refuses without MILO_LIVE=1
            except PermissionError as exc:
                raise SystemExit(f"refusing to run: {exc}")
            cvecs = emb.embed([c["text"] for c in chunks])  # batches of 64, retried at the one retry site
            qvecs = emb.embed([q["question"] for q in queries])
            ctok = emb.total_tokens
            rows = []
            for q, qv in zip(queries, qvecs):
                scored = sorted(((cos(qv, cv), c["chunk_id"]) for cv, c in zip(cvecs, chunks)), reverse=True)
                ranked = [cid for _, cid in scored]
                m = per_question_metrics(ranked, set(q["relevant"]))
                m.update({"qid": q["qid"], "type": q["type"], "relevant": q["relevant"]})
                rows.append(m)
            cost = emb.total_cost_usd  # the response's own cost, else tokens times the fallback price
            total_cost += cost
            per_model[model] = rows
            summary = summarise(rows)
            summary["tokens"] = ctok
            summary["cost_unknown_calls"] = emb.cost_unknown_calls
            summary["cost_usd"] = round(cost, 6)
            cfg["models"][model] = {"summary": summary, "rows": rows}
            if no_match:
                cfg["models"][model]["summary_excluding_no_chunk"] = summarise(rows, no_match)
            print(f"[{size}/{overlap}] {model:32s} nDCG@5 {summary['ndcg5']['mean']:.3f}  MRR {summary['mrr']['mean']:.3f}"
                  f"  R@5 {summary['recall5']['mean']:.3f}  cost ${cost:.5f}")
        cfg["paired_vs_reference"] = paired(cfg["models"], REFERENCE)
        if no_match:
            cfg["paired_excluding_no_chunk"] = paired(cfg["models"], REFERENCE, no_match)
        results["configs"].append(cfg)
    inputs = {"gold": common.GOLD_PATH, "script": Path(__file__), "protocol": PROTOCOL}
    for md in sorted((REPO / "data" / "kb").rglob("*.md")):
        inputs[f"kb/{md.relative_to(REPO / 'data' / 'kb').as_posix()}"] = md
    prov = common.provenance(model=",".join(m for m, _ in MODELS), seed=SEED, temperature=None,
                             command=f"MILO_LIVE=1 python evals/retrieval/compare_embeddings.py --max-cost {args.max_cost}",
                             inputs=inputs)
    results = {**prov, **results}
    results.update({"models": [{"slug": m, "listed_usd_per_token": p} for m, p in MODELS],
                    "temperature_note": "embeddings have no temperature",
                    "seed_note": "no sampling; seed 0 is the bootstrap seed",
                    "spend_reason": SPEND_REASON, "max_cost_usd": args.max_cost,
                    "split": "dev", "k": K, "bootstrap": BOOT, "seed": SEED, "reference": REFERENCE,
                    "query_prefix": "none", "total_cost_usd": round(total_cost, 6),
                    "protocol": "evals/protocols/e01-rerun.md", "date": time.strftime("%Y-%m-%d")})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / RESULT_NAME).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"TOTAL cost this run: ${total_cost:.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
