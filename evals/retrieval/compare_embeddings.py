"""Embedding comparison on the dev split (SPEC D5). Uses OpenRouter embeddings. Never reads the test split.

Run from the repo root: OPENROUTER_API_KEY must be set. Writes evals/results/retrieval/embedding_dev.json.
"""
import hashlib, json, math, os, random, re, sys, time, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("EMBED_OUT", REPO / "evals" / "results" / "retrieval"))
KEY = os.environ["OPENROUTER_API_KEY"]
MODELS = [
    ("openai/text-embedding-3-small", 0.00000002),
    ("openai/text-embedding-3-large", 0.00000013),
    ("qwen/qwen3-embedding-8b", 0.00000001),
    ("google/gemini-embedding-2", 0.0000002),
    ("mistralai/mistral-embed-2312", 0.0000001),
]
K = 5

def norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()

def chunks_from_kb():
    out = []
    for md in sorted((REPO / "data" / "kb").rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        doc_id = md.relative_to(REPO / "data" / "kb").with_suffix("").as_posix()
        body = text.split("---", 2)[2] if text.startswith("---") else text
        # heading-aware sections, packed to <= 800 chars with 100 overlap (SPEC D9)
        sections, cur = [], []
        for line in body.splitlines():
            if line.startswith("#") and cur:
                sections.append("\n".join(cur)); cur = []
            cur.append(line)
        if cur: sections.append("\n".join(cur))
        for sec in sections:
            sec = sec.strip()
            start = 0
            while start < len(sec):
                piece = sec[start:start + 800]
                out.append((doc_id, piece))
                if start + 800 >= len(sec): break
                start += 700
    return [{"chunk_id": f"{d}#{i}", "doc_id": d, "text": t} for i, (d, t) in enumerate(out)]

def embed(model, texts):
    body = json.dumps({"model": model, "input": texts}).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/embeddings", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.load(r)
            vecs = [x["embedding"] for x in sorted(d["data"], key=lambda x: x["index"])]
            return vecs, d.get("usage", {}).get("total_tokens", 0)
        except Exception as e:
            if attempt == 2: raise
            time.sleep(2 + attempt * 3)

def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(y*y for y in b)))

def ndcg_at_k(ranked_ids, relevant, k):
    dcg = sum(1 / math.log2(i + 2) for i, c in enumerate(ranked_ids[:k]) if c in relevant)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / idcg if idcg else 0.0

def boot_ci(vals, seed=0, n=2000):
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(vals) for _ in vals) / len(vals) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]

def main():
    chunks = chunks_from_kb()
    gold = [json.loads(l) for l in (REPO / "data" / "gold.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    dev = [g for g in gold if g["split"] == "dev" and g["gold_evidence"]]
    # relevant chunks: chunks containing the gold quote, in its document
    queries = []
    for g in dev:
        rel = set()
        for ev in g["gold_evidence"]:
            q = norm(ev["quote"])
            for c in chunks:
                if c["doc_id"] == ev["doc_id"] and q in norm(c["text"]):
                    rel.add(c["chunk_id"])
        queries.append({"qid": g["qid"], "type": g["type"], "question": g["question"], "relevant": sorted(rel)})
    missing = [q["qid"] for q in queries if not q["relevant"]]
    print(f"chunks={len(chunks)} dev_answerable={len(queries)} no_chunk_match={missing}")
    results = {}
    for model, price in MODELS:
        t0 = time.time()
        cvecs, ctok = [], 0
        for i in range(0, len(chunks), 64):
            v, t = embed(model, [c["text"] for c in chunks[i:i+64]]); cvecs += v; ctok += t
        qvecs, qtok = embed(model, [q["question"] for q in queries]); ctok += qtok
        rows, recalls, ndcgs = [], [], []
        for q, qv in zip(queries, qvecs):
            scored = sorted(((cos(qv, cv), c["chunk_id"]) for cv, c in zip(cvecs, chunks)), reverse=True)
            top = [cid for _, cid in scored[:K]]
            rel = set(q["relevant"])
            r = 1.0 if rel & set(top) else 0.0
            n = ndcg_at_k(top, rel, K)
            recalls.append(r); ndcgs.append(n)
            rows.append({"qid": q["qid"], "type": q["type"], "top5": top, "hit": r, "ndcg5": round(n, 6)})
        cost = ctok * price
        rlo, rhi = boot_ci(recalls); nlo, nhi = boot_ci(ndcgs)
        results[model] = {
            "recall_at_5": round(sum(recalls)/len(recalls), 4), "recall_ci": [round(rlo,4), round(rhi,4)],
            "ndcg_at_5": round(sum(ndcgs)/len(ndcgs), 4), "ndcg_ci": [round(nlo,4), round(nhi,4)],
            "tokens": ctok, "cost_usd": round(cost, 6), "seconds": round(time.time()-t0, 1), "rows": rows,
        }
        print(f"{model:34s} recall@5 {results[model]['recall_at_5']:.3f} {results[model]['recall_ci']}  "
              f"nDCG@5 {results[model]['ndcg_at_5']:.3f} {results[model]['ndcg_ci']}  tokens {ctok}  cost ${cost:.5f}")
    total_cost = sum(v["cost_usd"] for v in results.values())
    doc = {"split": "dev", "k": K, "chunk_count": len(chunks), "query_count": len(queries),
           "chunk_sha256": hashlib.sha256(json.dumps(chunks, sort_keys=True).encode()).hexdigest(),
           "query_sha256": hashlib.sha256(json.dumps(queries, sort_keys=True).encode()).hexdigest(),
           "query_prefix": "none (no instruction for any model)", "total_cost_usd": round(total_cost, 6),
           "models": results}
    (OUT / "embedding_dev_results.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"TOTAL embedding cost this run: ${total_cost:.5f}")

if __name__ == "__main__":
    main()
