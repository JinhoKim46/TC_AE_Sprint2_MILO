# Live batch requests to the integrator

1. **M1 `min_score` (settings.py).** The calibrated value from `evals/results/retrieval/retrieval_dev.json` is
   **0.73** (`calibration.chosen`, the balanced-accuracy rule of D25). Caveat for the decision: the classes are not
   separable on dev (answerable top-1 minimum 0.638, unanswerable top-1 maximum 0.756), and at 0.73 only 56 percent of
   answerable dev questions are answered while 83 percent of unanswerable ones abstain. Request: set
   `MIN_SCORE = 0.73` in `src/milo/settings.py`, or record why 0.30 stays. The M7 generation in this batch ran with
   the current 0.30 (settings.py unchanged).
