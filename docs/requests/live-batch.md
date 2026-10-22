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
