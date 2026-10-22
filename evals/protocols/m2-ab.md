# Protocol M2-AB: advanced retrieval arms against the dense baseline

| Field | Value |
|---|---|
| Protocol id | `m2-ab` |
| Status | committed before any result file (D83) |
| Date | 2026-10-22 |
| Owning milestone | M2 (advanced RAG) |
| Implements | SPEC section 4, D8, D10, D11, D12, D24, D56, D83, D126, D148, D151; section 10.3; HARNESS rules 1, 6, 14 |
| Produces | `evals/retrieval/ab.py`, `evals/results/retrieval/ab_dry.json` (offline fixture, never a MILO result), `evals/results/retrieval/ab_dev.json` (live, run by the integrator) |
| Measured calls | rewrite, self-query and rerank use the chat model through `milo.llm.cloud`; estimate and cap below |

## Question

Does any M2 arm retrieve better than the M1 dense baseline (D7: top 5 by cosine, `google/gemini-embedding-2`,
800/100 chunks), enough to justify the extra model call or the extra code in `MILO_ADVANCED_RAG=1` mode?

| Arm | What it does | Extra model call per question |
|---|---|---|
| `dense` | M1 baseline: embed the question, rank all chunks by cosine | none |
| `rewrite` | D10: the chat model rewrites the question into one standalone query; dense ranking of the rewrite | 1 chat |
| `selfquery` | D11: the chat model returns JSON `{section?, product?, updated_after?}`; dense ranking inside the filter; a parse failure, a value outside the knowledge base vocabulary, or an empty filtered ranking falls back to the unfiltered ranking | 1 chat |
| `rewrite_selfquery` | D10 then D11 on the rewritten query (D11 is on only with D10): the advanced-mode retrieval as the product would run it | 2 chat (the rewrite is shared with `rewrite`) |
| `hybrid` | D8: BM25 over the same chunks fused with the dense ranking by reciprocal rank fusion, k = 60, on the original question | none |
| `rerank` | D12: dense top 10 re-ordered by the chat model in one listwise call; the rest of the ranking follows unchanged | 1 chat |

## Data

- Gold rows through `evals/common.py` (`load_gold("dev")`). The test split is never read here. The A/B is
  computed on the dev rows with non-empty `gold_evidence`; the unanswerable dev rows are run through every arm
  too and their rows are stored, but they carry no metric.
- Knowledge base `data/kb/**/*.md`, chunked 800/100 (D9); the same chunks feed dense and BM25. Relevance, metrics
  and the per-question rule follow `e01-rerun.md` (binary relevance by gold quote; recall@5, nDCG@5, MRR and AP
  over the full ranking, P@5). Rows whose quote lands in no chunk count as a miss in every arm.
- Gold filters (D151) use `section` and `product` only. They are used for one secondary measure, the filter
  accuracy of the self-query arms; they are never given to an arm as input.
- Dry mode: the committed fixture `evals/retrieval/fixtures/ab_dry_fixture.json` (invented content; a fake hashed
  embedder; scripted rewrites, filters and rerank orders). It tests the harness and the replay and says nothing
  about MILO.

## Procedure

1. Build or load the index for the product embedding model (M1 `make index`); build BM25 over the same chunks.
2. For every dev question: dense ranking; the rewrite (cached once per question); the self-query reply on the
   original and on the rewritten query; the rerank reply on the dense top 10. Every model reply is stored in the
   row (D56) with its tokens, so a replay never calls a model.
3. Rank every chunk per arm, compute the five metrics per question and arm.
4. Per arm: mean with a bootstrap 95 percent CI (2000 resamples, seed 0, D24); by question type; the **paired**
   bootstrap of the per-question difference (arm minus dense) for each metric, resampling question indices
   jointly (same seed, same draws for every arm).
5. Write `ab_dev.json` with `git_commit`, `model` (chat and embedding slugs), `seed` 0, `temperature` 0.0,
   `command`, `timestamp`, `inputs` (SHA-256 of the gold file, the KB files, the prompts in `src/milo/rag/` and
   the script), `spend_reason`, `total_cost_usd`, `rows`.
6. `--replay` recomputes every summary and paired interval from `rows` and matches exactly. A dry result is also
   regenerated from the fixture and compared row by row. Live reruns of model-driven arms are reported, not failed
   (SPEC 10.3).

## Decision rule (per arm, on nDCG@5 at k = 5, dev)

- **Adopt** the arm in `MILO_ADVANCED_RAG=1` mode if the paired nDCG@5 difference CI lies entirely above zero.
- **Not adopted** if the CI includes zero or lies below. An arm with a model call must clear the bar; there is no
  tie-break in its favour, because it costs a call and latency (D12: no unmeasured claims).
- `hybrid` has no model call; it is adopted on the same rule, and when the CI includes zero but the mean
  difference is at least zero and recall@5 is not lower, it is reported as "no harm, not adopted".
- `rewrite_selfquery` is adopted only if it clears the bar and at least one of its two parts clears it alone.
- Reported as context, not decisive: the other four metrics, per type (identifier, paraphrase), the self-query
  filter accuracy against the gold filters, the fallback rate, and the rate at which a filter excluded every
  relevant chunk (a harmful filter).
- The decision is read once, from `ab_dev.json`. The single test-split run happens at F2 (`run_final.py`), for the
  adopted configuration only; it is not used to choose.

## Measured-call budget

The chat model is the dev model chosen by E02 (D3, D148), not the D4 product model; the integrator passes its
slug and listed prices. Per dev question (60 rows): rewrite about 300 tokens in and 40 out; self-query about 450
in and 50 out, twice; rerank about 2,800 in and 80 out (ten chunks of about 250 tokens). Total about 255,000
tokens (about 240,000 in, 15,000 out). At 0.30 and 1.20 USD per million (a small dev model) that is about 0.09 USD;
at 3 and 15 USD per million it would be about 0.95 USD, so a priced-up model needs a higher cap and a reason.
Embeddings: 60 rewritten queries, a few hundred tokens, below 0.001 USD (chunk vectors come from the M1 index).
`--max-cost` defaults to nothing: the live run refuses without it. Recommended: 0.25 USD. The run stops and
records a partial result if the running cost passes the cap. Reason recorded in the result file: "advanced arms
need the dev chat model's rewrite, filter and rerank replies for the dev questions (D126)".

## Limits

- 48 answerable questions cannot separate differences smaller than about 0.05 nDCG@5; the rule is conservative.
- Abstention (`min_score`) is not part of this A/B: it was calibrated for dense scores in M1. Fusion and rerank
  scores are not cosines, so an advanced mode keeps the dense top-1 score for the abstain gate. Not measured here.
- Single-quote binary relevance understates recall when several chunks answer.
- Model replies at temperature 0 are not bit-reproducible; the stored rows are the record.
