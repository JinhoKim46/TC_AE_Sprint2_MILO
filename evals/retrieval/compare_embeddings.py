"""Embedding comparison on the dev split (SPEC D5, D24). Uses OpenRouter embeddings.

Run from the repo root with OPENROUTER_API_KEY set:
    python evals/retrieval/compare_embeddings.py

Metrics per question (binary relevance; a chunk is relevant when it contains the gold quote):
    recall@5, nDCG@5, MRR (full ranking), precision@5, average precision (full ranking).
Comparisons: paired bootstrap of the per-question difference between each model and the chosen
reference model (2000 resamples, seed 0). Chunk configurations: 800/100, 400/50, 1500/150 characters.
Never reads the test split. Writes evals/results/retrieval/embedding_dev_v2.json.
"""
import hashlib, json, math, os, random, re, time, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("EMBED_OUT", REPO / "evals" / "results" / "retrieval"))
KEY = os.environ["OPENROUTER_API_KEY"]
MODELS = [
    ("google/gemini-embedding-2", 0.0000002),
    ("openai/text-embedding-3-large", 0.00000013),
    ("mistralai/mistral-embed-2312", 0.0000001),
    ("qwen/qwen3-embedding-8b", 0.00000001),
    ("openai/text-embedding-3-small", 0.00000002),
]
CHUNK_CONFIGS = [(800, 100), (400, 50), (1500, 150)]
REFERENCE = "google/gemini-embedding-2"
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


def embed(model, texts):
    body = json.dumps({"model": model, "input": texts}).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/embeddings", data=body,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.load(r)
            vecs = [x["embedding"] for x in sorted(d["data"], key=lambda x: x["index"])]
            return vecs, d.get("usage", {}).get("total_tokens", 0)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 + attempt * 3)


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


def main():
    gold = [json.loads(l) for l in (REPO / "data" / "gold.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    dev = [g for g in gold if g["split"] == "dev" and g["gold_evidence"]]
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
            cvecs, ctok = [], 0
            for i in range(0, len(chunks), 64):
                v, t = embed(model, [c["text"] for c in chunks[i:i + 64]])
                cvecs += v; ctok += t
            qvecs, qtok = embed(model, [q["question"] for q in queries])
            ctok += qtok
            rows = []
            for q, qv in zip(queries, qvecs):
                scored = sorted(((cos(qv, cv), c["chunk_id"]) for cv, c in zip(cvecs, chunks)), reverse=True)
                ranked = [cid for _, cid in scored]
                m = per_question_metrics(ranked, set(q["relevant"]))
                m.update({"qid": q["qid"], "type": q["type"]})
                rows.append(m)
            cost = ctok * price
            total_cost += cost
            per_model[model] = rows
            summary = {}
            for key in ("recall5", "ndcg5", "mrr", "p5", "ap"):
                vals = [r[key] for r in rows]
                summary[key] = {"mean": round(mean(vals), 4), "ci": boot_ci(vals)}
            summary["tokens"] = ctok
            summary["cost_usd"] = round(cost, 6)
            cfg["models"][model] = {"summary": summary, "rows": rows}
            print(f"[{size}/{overlap}] {model:32s} nDCG@5 {summary['ndcg5']['mean']:.3f}  MRR {summary['mrr']['mean']:.3f}"
                  f"  R@5 {summary['recall5']['mean']:.3f}  cost ${cost:.5f}")
        ref_rows = per_model[REFERENCE]
        for model, _ in MODELS:
            if model == REFERENCE:
                continue
            pd = {}
            for key in ("recall5", "ndcg5", "mrr", "p5", "ap"):
                pd[key] = paired_diff([r[key] for r in per_model[model]], [r[key] for r in ref_rows])
            cfg["paired_vs_reference"][model] = pd
        results["configs"].append(cfg)
    results.update({"split": "dev", "k": K, "bootstrap": BOOT, "seed": SEED, "reference": REFERENCE,
                    "query_prefix": "none", "total_cost_usd": round(total_cost, 6),
                    "command": "OPENROUTER_API_KEY=... python evals/retrieval/compare_embeddings.py",
                    "date": "2026-10-22"})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "embedding_dev_v2.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"TOTAL cost this run: ${total_cost:.5f}")


if __name__ == "__main__":
    main()
