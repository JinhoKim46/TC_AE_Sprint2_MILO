# M2 report: advanced retrieval (query rewrite, self-query, BM25 hybrid, rerank)

Status: code and protocol complete; offline checks pass. The live A/B on the dev split has not run (SPEC D126:
live arms run in the integrator's batch), so this report carries **no MILO retrieval numbers yet** and takes no
decision. The decision per arm is read from `evals/results/retrieval/ab_dev.json` under the rule below.

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

## To fill after the live batch

The A/B table (nDCG@5, recall@5, MRR, P@5, AP with CIs and paired differences), the decision per arm, the weakest
question type per arm, self-query filter accuracy and fallback rate, spend, and the live command with its commit.
