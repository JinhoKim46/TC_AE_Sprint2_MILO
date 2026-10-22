# Live batch spend ledger (2026-10-22)

First paid OpenRouter runs of MILO. Owner approval 2026-10-22 (balance 8.47 USD); batch hard cap **3.00 USD**
(integrator decision 67). Dev split only; the test split is not read. Rule before each run: cumulative actual
plus the run's cap must be at most 3.00 USD, else the cap is lowered to fit or the run is skipped.

Actual cost is the delta of the key's `usage` field on OpenRouter's free `/key` endpoint, read before and after
each run (baseline 8.473260 USD lifetime usage), cross-checked against the result file's own cost total.

Nominal protocol caps add up to 3.30 USD (E01 0.05, M1 about 0.005, E02 1.20, M2 0.25, M3 0.30, M4 0.60, SR 0.50,
M7 generation 0.40), above the batch cap; the later caps (M4, SR, M7) are trimmed once the earlier actuals are known.

| # | Run | Command | Protocol (commit) | Cap USD | Actual USD | Result file cost | Cumulative USD |
|---|---|---|---|---|---|---|---|
| 1 | E01c embedding rerun | `MILO_LIVE=1 python evals/retrieval/compare_embeddings.py --max-cost 0.05` | e01-rerun.md (ac31ea3) | 0.05 | 0.015496 | 0.015722 | 0.015496 |

## Notes

- E01 runner: `compare_embeddings.py` gained the D56 provenance block, the spend reason, `relevant` per row, the
  summary excluding rows without a chunk, a running `--max-cost` (default 0.05) and `--replay`. Metric and chunk code
  unchanged (E01b comparability, as the protocol asks).
- E01 decision (e01-rerun.md rule, 800/100, nDCG@5, 48 dev questions): `google/gemini-embedding-2` stays; no
  candidate's paired interval lies above zero. Gemini 0.761; runner-up `mistralai/mistral-embed-2312` 0.674, paired
  difference -0.087 [-0.176, -0.004] (excluded); `openai/text-embedding-3-large` -0.089 [-0.192, 0.009] (interval
  still includes zero). D5 restated as "confirmed on 48 dev questions, runner-up excluded". Chunk setting stays
  800/100 (D9); no winner differs from the provisional choice, so M1 indexes with gemini at 800/100.
