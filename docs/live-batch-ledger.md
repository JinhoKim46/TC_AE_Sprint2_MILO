# Live batch spend ledger (2026-10-22)

First paid OpenRouter runs of MILO. Owner approval 2026-10-22 (balance 8.47 USD); batch hard cap **3.00 USD**
(integrator decision 67). Dev split only; the test split is not read. Rule before each run: cumulative actual
plus the run's cap must be at most 3.00 USD, else the cap is lowered to fit or the run is skipped.

Actual cost is the result file's own total (the OpenRouter `usage.cost` field of each response). The key's `usage`
figure on OpenRouter's free `/key` endpoint (baseline 8.473260 USD lifetime usage) is read before and after each run
as a cross-check; it lags by a minute or so, so it is compared cumulatively, not per run.

Nominal protocol caps add up to 3.30 USD (E01 0.05, M1 about 0.005, E02 1.20, M2 0.25, M3 0.30, M4 0.60, SR 0.50,
M7 generation 0.40), above the batch cap; the later caps (M4, SR, M7) are trimmed once the earlier actuals are known.

| # | Run | Command | Protocol (commit) | Cap USD | Actual USD | Result file cost | Cumulative USD |
|---|---|---|---|---|---|---|---|
| 1 | E01c embedding rerun | `MILO_LIVE=1 python evals/retrieval/compare_embeddings.py --max-cost 0.05` | e01-rerun.md (ac31ea3) | 0.05 | 0.015722 | 0.015722 | 0.015722 |
| 2 | M1 index build | `MILO_LIVE=1 make index` | e01-rerun.md (ac31ea3) | 0.005 | about 0.0021 (no result file; key usage delta) | none | 0.0178 |
| 3 | M1 dense dev eval | `MILO_LIVE=1 make eval-retrieval` | e01-rerun.md (ac31ea3) | 0.005 | 0.00225 | 0.00225 | 0.0201 |
| 4a | E02 chat compare, attempt 1 | `MILO_LIVE=1 make eval-chat` | e02-chat-compare.md (ac31ea3) | 1.20 (plan check) | 0 (failed at the first embedding request, before any chat call) | none | 0.0201 |
| 4b | E02 chat compare, dev (retry) | `MILO_LIVE=1 make eval-chat` | e02-chat-compare.md (ac31ea3) | 1.20 (plan 0.195) | 0.175178 | 0.172928 chat + 0.00225 embedding | 0.1953 |
| 5 | M2 A/B, dev | `MILO_LIVE=1 make eval-ab AB_ARGS="--max-cost 0.25 --chat-model anthropic/claude-haiku-5.5 --price-in 0.10 --price-out 0.50 --reason ..."` | m2-ab.md (2e1abdc) | 0.25 | 0.01328 (key usage delta; includes query embeddings) | 0.01214 chat | 0.2086 |

## Notes

- E01 runner: `compare_embeddings.py` gained the D56 provenance block, the spend reason, `relevant` per row, the
  summary excluding rows without a chunk, a running `--max-cost` (default 0.05) and `--replay`. Metric and chunk code
  unchanged (E01b comparability, as the protocol asks).
- E01 decision (e01-rerun.md rule, 800/100, nDCG@5, 48 dev questions): `google/gemini-embedding-2` stays; no
  candidate's paired interval lies above zero. Gemini 0.761; runner-up `mistralai/mistral-embed-2312` 0.674, paired
  difference -0.087 [-0.176, -0.004] (excluded); `openai/text-embedding-3-large` -0.089 [-0.192, 0.009] (interval
  still includes zero). D5 restated as "confirmed on 48 dev questions, runner-up excluded". Chunk setting stays
  800/100 (D9); no winner differs from the provisional choice, so M1 indexes with gemini at 800/100.
- M1: `min_score` calibrated to 0.73 but not separable on dev; filed as request 1 in `docs/requests/live-batch.md`
  (settings.py unchanged).
- E02 attempt 1 failed with HTTP 400 at task B's first embedding request: the runner sent all 167 chunks in one
  request and the provider caps a batch at 100. Deterministic runner defect, not a provider fault, no chat call made.
  Fix (own commit, before the result): task B embeds through `milo.rag.embed.OpenRouterEmbedder` (batches of 64,
  the one retry site) and the result header records `embedding_cost_usd` and `chat_cost_usd`. One retry follows.
- E02 has no running `--max-cost`; its cap mechanism is the up-front plan check against the protocol cap of 1.20 USD
  (planned cost from catalogue prices with a 1.25 margin). All six slugs exist in the catalogue as named.
- E02 decision (rule of e02-chat-compare.md, dev, reference `openai/gpt-4.1-mini` F1 0.572): development model
  **`anthropic/claude-haiku-5.5`** (0.10 / 0.50 USD per million in/out, context 1,000,000), the only passing
  candidate: F1 0.546 (paired CI [-0.102, 0.039]), JSON 1.00, citation 0.854, abstention 0.929, no provider error.
  Failed: deepseek-v4-flash (F1 0.468), gpt-4.1-nano (F1, citation 0.396, abstention), gemini-2.5-flash-lite
  (F1, JSON 0.81, 9 provider errors), gpt-5-nano (all 114 calls ended in a permanent provider error; the runner
  stores only `provider_error:unavailable`, so the cause is not recorded; most likely the fixed temperature 0 or
  the disabled reasoning, which the protocol says should fall back to the model default; not retried, not retuned).
- M7 judges (m7-judge.md Data 4): fewer than two candidates passed, so the places are the passing candidate and the
  reference: **`anthropic/claude-haiku-5.5`** and **`openai/gpt-4.1-mini`** (0.40 / 1.60). Neither is the product model.
- E02 skipped steps: step 7 (single test-split run, needs `MILO_FINAL=1`) is not run because this batch is dev only;
  step 8 (blind answer grading) is session work outside this batch.
- M2: no arm adopted (rerank +0.072 with CI [-0.019, 0.167]; every other arm significantly worse).
