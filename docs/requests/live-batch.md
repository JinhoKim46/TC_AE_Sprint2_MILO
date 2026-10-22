# Live batch requests to the integrator

1. **M1 `min_score` (settings.py).** The calibrated value from `evals/results/retrieval/retrieval_dev.json` is
   **0.73** (`calibration.chosen`, the balanced-accuracy rule of D25). Caveat for the decision: the classes are not
   separable on dev (answerable top-1 minimum 0.638, unanswerable top-1 maximum 0.756), and at 0.73 only 56 percent of
   answerable dev questions are answered while 83 percent of unanswerable ones abstain. Request: set
   `MIN_SCORE = 0.73` in `src/milo/settings.py`, or record why 0.30 stays. The M7 generation in this batch ran with
   the current 0.30 (settings.py unchanged).
2. **Product model refuses disabled reasoning (blocks M3, M7 generation; batch stopped).** Every call to
   `anthropic/claude-sonnet-5.5` (catalogue price 2.00 / 10.00 USD per million, context 1,000,000) returned HTTP 400
   "Reasoning is mandatory for this endpoint and cannot be disabled", twice, at 0 USD. `src/milo/llm/cloud.py` always
   sends `reasoning: {"enabled": false}` when `disable_thinking` is true (D23b). Request: decide whether the product
   model runs with reasoning on (thinking stripped by `strip_think`, higher output cost; the M3 cap of 0.30 and the M7
   generation cap of 0.40 should be re-estimated) and amend `m3-tools.md` step 3 and D23b before a rerun; or a
   per-model capability check in `cloud.py`. Remaining runs after the fix: M3, M4 (dev model, unaffected), SR A-01b and
   A-03 (dev model, unaffected), M7 generation. Batch spend so far 0.2086 USD of the 3.00 cap.
3. **E02 gpt-5-nano.** All 114 calls ended in a permanent provider error; the E02 runner stores only
   `provider_error:unavailable`, not the provider message. Likely the same class of refusal (temperature 0 or disabled
   reasoning). The protocol asks such a model to run at its fixed default; the runner does not. The E02 decision does
   not change (gpt-5-nano is not needed for it), but the drop reason should be recorded properly.

## Hybrid investigation

Question: the M2 hybrid arm scored nDCG@5 0.493 against dense 0.761 (paired difference -0.268, CI
[-0.382, -0.158]). Is it a bug? Finding: **no bug found; the arm is weak because BM25 is weak on these questions.**
No live call, no change to the committed result.

Checks (all offline, from `evals/results/retrieval/ab_dev.json` rows and the KB):

- **Same chunk ids.** Rebuilding the chunks at 800/100 gives 167 chunks with `chunk_sha256` 2e039419865c..., equal
  to the result's. `BM25Index(chunks)` and the dense `VectorStore` are built from that one list in
  `ab.run_arms`; ids are `doc_id#index` in both. A FakeEmbedder test (`tests/test_fusion.py`) asserts BM25 ids are
  a subset of the dense ids and the fused ids are their union.
- **Fusion.** `rrf` is rank-based, k = 60 (`rrf_k` 60 in the result), 1-based ranks, no score mixing; the test
  recomputes every fused score as the sum of `1/(60+rank)` terms. Dense is the full 167-chunk ranking, BM25 the
  chunks sharing a token with the query (mostly 150 to 167, because stop words match).
- **Tokenisation and scoring.** Lower-cased `[a-z0-9]+` tokens, `BM25Okapi` defaults, as D8 says. Nothing
  malformed. Removing stop words was tried offline and does not help (BM25-only mean rank of the gold chunk 46
  against 27, 11 questions with no match against 1), so it is not the cause.
- **Where the loss comes from.** Gold-chunk rank on the 48 answerable dev questions: dense mean 3.0, median 1,
  top-5 in 92 percent; BM25 alone mean 26.6, median 8.5, top-5 in 42 percent (one gold chunk missing from the BM25
  list altogether, q010). The 36 paraphrase questions are written to avoid the KB's wording (q009: dense 1, BM25
  69, hybrid 12; q058: 1, 87, 17; q096: 1, 99, 18). With k = 60 and two full-length lists, a chunk at dense rank 1
  and BM25 rank 69 (1/61 + 1/129 = 0.0241) loses to chunks that are mid-list in both, so a weak list pulls the
  fused order down. By type: identifier nDCG 0.564 (dense 0.797), paraphrase 0.469 (dense 0.749).

Conclusion: D8 hybrid as specified (unfiltered BM25 over all chunks, RRF k = 60, equal weights) is a poor fit for
this paraphrase-heavy gold set, not a defect. Possible follow-ups for the integrator, not done here: a BM25 list
cut to its top 10 to 20 before fusion, or a lower BM25 weight. Each is a change to D8 and needs a new A/B in part 2;
the part 2 rerun of the current arm should reproduce about 0.49.

## Reasoning per model (D159), implemented offline

`cloud.REASONING_REQUIRED` (starts with `anthropic/claude-sonnet-5.5`) gets `reasoning: {effort: "low"}`; every
other model keeps `{enabled: false}`. A 400 "reasoning is mandatory ... cannot be disabled" from any model raises
`ReasoningRequired` (`provider_error:reasoning_required:<model>`), which is never retried. Result provenance has a
`reasoning` map (`off` or `low_effort` per slug). E02 rows keep the provider message in `provider_message`
(decision 72). Not done: reading `supported_parameters` from the catalogue (the runners do not load it).
