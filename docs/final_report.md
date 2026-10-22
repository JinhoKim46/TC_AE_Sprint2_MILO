# F2 final report: the single test-split run

Plan: `docs/final-run-plan.md` (commit 45465be), committed before the code (12f8faa), the test labels (a98bc9d) and
the results (261b4ea). One invocation of `MILO_LIVE=1 make final-test` ran every step. The gold SHA-256 matched
`MANIFEST.json` first. `evals/results/test_access.log` has four `started` lines (one per test load) and exactly one
`ok` line. There was one attempt per run, no retry and no failed step. The labels were committed before the judge
step began, so the run did not pause at `awaiting-labels`. Code, prompts, thresholds, models and prices are the dev
ones. Every number below is copied from its result file, and `make eval` replays each file.

## Dev and test

| Run | Metric | Dev | Test | Target | Met | Cost USD |
|---|---|---|---|---|---|---|
| M1 dense (`retrieval_test.json`, 48 answerable of 60) | nDCG@5 [95% CI] | 0.761 [0.660, 0.848] | 0.629 [0.521, 0.731] | none; D5 is not revisited | reported | 0.002247 |
| | recall@5 / MRR | 0.917 / 0.716 | 0.833 / 0.574 | none | reported | |
| M2 (no arm adopted on dev, so the adopted configuration is dense; the run is M1's) | nDCG@5 | 0.761 | 0.629 | none | reported | 0 |
| M5G gate (`gate_test.json`, 142 in-scope, 68 off-topic) | in-scope refused | 0 of 142 | **1 of 142** (0.7%, at stage 2) | 0 | **no** | 0.004650 |
| | off-topic refused (both stages) | 67 of 68 (0.985) | **63 of 68 (0.926)** [0.853, 0.985] | at least 0.95 | **no** | |
| E02 step 7 (`chat_test.json`, 48 turns, 60 task-B rows) | task A F1, haiku / gpt-4.1-mini | 0.546 / 0.572 | 0.550 / 0.554 | haiku not more than 0.10 below the reference | yes | 0.094678 |
| | abstention correct, haiku / reference | 0.929 / 0.929 | 0.923 / 1.000 | (dev rule: within 0.05) | no (the runner's dev rule now picks the reference) | |
| M5b discovery (`discovery_test.json`, A-2, B-2, E-2, 48 turns) | premature-estimate rate / evidence verbatim | 0.0 / 1.0 | 0.0 / 1.0 | 0 / 1.0 | yes | 0.511972 |
| | fact P / R / F1 | 0.683 / 0.717 / 0.699 | 0.632 / 0.655 / 0.643 | F1 at least 0.85 is "fit" (not met on dev either) | no | |
| | state accuracy / repeated-question rate | 0.981 / 0.0 | 0.979 / 0.0 | none / 0 | yes | |
| M7 generation (`generation_test.json`, 30 questions) | label means F / R / CP | 0.967 / 1.80 / 0.26 | 0.867 / 1.77 / 0.24 | none | reported | 0.235936 |
| M7 judge haiku (`judge_test.json`) | kappa F / R / CP | 0.20 / 0.90 / 0.75 | 0.38 [0.10, 0.67] / 0.57 [0.29, 0.84] / 0.56 [0.41, 0.71] | kappa at least 0.60 and CI low at least 0.30 | none validated | 0.037960 (both judges) |
| M7 judge gpt-4.1-mini | kappa F / R / CP | 0.00 / 0.89 / 0.76 | 0.00 / 0.67 [0.29, 1.00] / 0.63 [0.50, 0.76] | same | CP only | |
| M3 tools, M4 attacks, SR | | 0.60 pass, ASR 0, SR pass | no test split | | not run | 0 |

F2 total: **0.887443 USD**, against a 2.50 cap and a 0.95 estimate. Live-batch ledger plus F2: 2.0845 USD.

## Misses, as measured

- **M5G misses both test targets.** One in-scope message was refused at stage 2, and 5 of 68 off-topic messages
  passed. Under m5g-gate.md the gate ships with the dev thresholds (T_refuse 0.37, T_pass 0.61). The gap goes to the
  checkpoint block, and the test split is not used to retune.
- **E02.** The step-7 rule holds: haiku's F1 is 0.004 below the reference, not more than 0.10. The dev decision
  rule, recomputed on test, fails haiku on abstention (0.923 against 1.000). The runner's `decision.line` therefore
  names the reference. The protocol's test rule is the binding one, so the development model stays haiku. This
  note records the disagreement.
- **M7.** No judge is usable on test, as on dev. Only gpt-4.1-mini's context precision is validated. Haiku's
  relevance and context precision were validated on dev but not on test. Faithfulness is low-prevalence (4 of 30
  unfaithful labels). The test labels came from one labelling agent and the dev labels from another.

## What generalised and what did not

The code-gated behaviour generalised exactly: the premature-estimate rate stayed 0, evidence stayed verbatim and
no question was repeated in the unseen conversations. State accuracy (0.98) and E02 task-A F1 (about 0.55) held as
well. Everything fitted or read on dev dropped. Dense retrieval lost 0.13 nDCG@5, and the two intervals overlap only
at their edges. M5b fact F1 fell by 0.06. The judge's agreement with the labels fell below the validation bar. The
gate's thresholds, set on dev rows where the in-scope and off-topic probabilities already overlapped (band 0.24
wide), let 5 off-topic test messages through and refused 1 in-scope one. The dev numbers were optimistic, by the
most for the parts tuned on dev (the gate thresholds, the M7 prompts written after the dev labels) and the least
for the parts enforced in code.
