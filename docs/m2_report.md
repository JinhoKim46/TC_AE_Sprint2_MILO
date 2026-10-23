# M2 report: advanced retrieval (query rewrite, self-query, BM25 hybrid, rerank)

Note (2026-10-23): numbers here describe the knowledge base before the D168 edit round; current dev numbers are in
`docs/rag-parameters.md`.

Status: code and protocol complete; offline checks pass. The live A/B on the dev split ran in the live batch on
2026-10-22 (`evals/results/retrieval/ab_dev.json`, `docs/live-batch-ledger.md`). **No arm is adopted.**

## What was built

| Piece | File | Notes |
|---|---|---|
| Rewrite (D10) | `src/milo/rag/translate.py` | `Rewriter` callable; live one goes through the chat model and `llm/retry.py`. An empty or failed reply keeps the original question. |
| Self-query (D11, D151) | `src/milo/rag/selfquery.py` | Pydantic `SelfQuery {section, product, updated_after}`, extra keys refused, values checked against the knowledge-base vocabulary. `self_query_rank` falls back to unfiltered search on a parse failure, an extractor error or an empty filtered result. |
| BM25 (D8) | `src/milo/rag/bm25.py` | `rank_bm25` over the same chunks; only chunks that share a token with the query enter the list; same filter keys as the vector store. |
| Fusion (D8) | `src/milo/rag/fusion.py` | Reciprocal rank fusion, k = 60, deterministic ties. |
| Rerank (D12) | `src/milo/rag/rerank.py` | Listwise, one call over the dense top 10; unusable reply keeps the dense order. A/B arm only. |
| A/B runner | `evals/retrieval/ab.py` | Six arms, five metrics, paired bootstrap against dense (2000 resamples, seed 0, same draws for every arm), decision per arm, D56 fields, dry, replay and live modes. |

Tests: `tests/test_selfquery.py` (parse, validation, the three fallbacks, no-filter case, rewrite guards) and
`tests/test_fusion.py` (RRF, BM25, rerank parser). Both run offline with scripted callables.

## Protocol and decision rule

`evals/protocols/m2-ab.md` (committed first, D83). An arm is adopted for `MILO_ADVANCED_RAG=1` only if its paired
nDCG@5 difference CI against the M1 dense baseline lies entirely above zero. Hybrid has a "no harm, not adopted"
outcome. `rewrite_selfquery` needs one of its parts to clear the bar alone. The single test-split run is at F2.

## Dry result (harness check, not evidence)

`evals/results/retrieval/ab_dry.json` runs the six arms on an invented 8-question fixture with a fake hashed
embedder and scripted replies chosen to exercise every path (a correct filter, an unparseable reply, a schema
violation, a filter that matches nothing, a filter that excludes the relevant chunk, unusable rerank replies).
`make eval-ab-replay` recomputes every metric and interval from the rows and regenerates the rows from the
fixture; both match exactly. Its numbers say nothing about MILO.

## Limits

- Abstention is not part of the A/B. Fusion and rerank scores are not cosines, so an advanced mode should keep the
  dense top-1 cosine for the `min_score` gate; this was not measured.
- 48 answerable dev questions cannot separate differences below about 0.05 nDCG@5.
- Model arms are reported with their live rerun, not failed on a difference (SPEC 10.3).
- `--max-cost` is checked between questions, so one question's calls (about 0.004 USD) can overshoot it.
- The chat model slug and prices are inputs of the live run (E02 has not pinned a dev model, D148).

## Live A/B result (dev, 48 answerable questions, k = 5)

Command: `MILO_LIVE=1 python evals/retrieval/ab.py --max-cost 0.25 --chat-model anthropic/claude-haiku-5.5`
(price 0.10 / 0.50 USD per million, the E02 development model), result at commit `7a0b203`, embedding
`google/gemini-embedding-2`, 800/100, 167 chunks. Spend 0.0121 USD chat (97,793 in, 4,721 out tokens) plus about
0.001 USD for 60 rewritten-query embeddings. `make eval-ab-replay` matches.

| Arm | nDCG@5 [95% CI] | recall@5 | MRR | Paired nDCG@5 diff vs dense [CI] | Decision |
|---|---|---|---|---|---|
| dense (baseline) | 0.761 [0.660, 0.848] | 0.917 | 0.716 | - | baseline |
| rewrite | 0.635 [0.516, 0.743] | 0.771 | 0.615 | -0.126 [-0.209, -0.052] | not adopted (worse) |
| selfquery | 0.571 [0.451, 0.692] | 0.688 | 0.535 | -0.190 [-0.320, -0.065] | not adopted (worse) |
| rewrite_selfquery | 0.481 [0.360, 0.598] | 0.625 | 0.447 | -0.280 [-0.413, -0.157] | not adopted (worse) |
| hybrid | 0.493 [0.374, 0.607] | 0.646 | 0.471 | -0.268 [-0.382, -0.158] | not adopted (worse; not "no harm") |
| rerank | 0.833 [0.747, 0.907] | 0.938 | 0.803 | +0.072 [-0.019, 0.167] | not adopted (CI includes zero) |

Per the rule, `MILO_ADVANCED_RAG=1` adopts nothing; the dense baseline stays. Rerank is the only arm with a positive
mean; 48 questions cannot separate it from dense.

Context (not decisive): weakest type is paraphrase for every arm. Self-query applied a filter on 38 of 60 questions
(fallbacks: 7 empty results, 1 unknown section), matched the gold filter exactly on 5 of 22 gold-filtered rows, set a
filter on 19 rows with no gold filter, and excluded every relevant chunk on 14 rows (harmful filter); with the rewrite
first: 44 applied, 6 of 22 exact, 14 harmful. Rewrite changed all 60 questions. Rerank: 0 unusable replies.

Note for review: hybrid falls 0.27 below dense, more than BM25 fusion usually costs. The result is reported as
measured and nothing was retuned; a check of the BM25 tokeniser and the "only chunks sharing a token" candidate list
is suggested before any later hybrid claim.
