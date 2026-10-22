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

## Notes

- E01 runner: `compare_embeddings.py` gained the D56 provenance block, the spend reason, `relevant` per row, the
  summary excluding rows without a chunk, a running `--max-cost` (default 0.05) and `--replay`. Metric and chunk code
  unchanged (E01b comparability, as the protocol asks).
