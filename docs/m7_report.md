# M7 report: RAG evaluation with an LLM judge

Status: harness, protocol and offline checks complete. The live generation, the labels and the judge runs have not
happened (SPEC D126: live runs are the integrator's batch; labels come from a separate labelling agent, D26).
Every MILO number below reads **pending live run**; the only numbers that exist are from the invented dry fixture
and say nothing about MILO.

## What was built

| Piece | File |
|---|---|
| Protocol (committed first, D83) | `evals/protocols/m7-judge.md` |
| Sampler: 30 dev qids, stratified by type, seed 26 | `evals/rag/sample.py`, `evals/rag/sample_dev.json` |
| Generation through injected callables | `evals/rag/generate.py` |
| Label schema, validator, template | `evals/rag/schema.py`, `evals/rag/labels.template.jsonl` |
| Judge prompts (placeholders until the labels exist) | `evals/rag/prompts/*.v1.md` |
| Judge runner, agreement, order check | `evals/rag/judge.py`, `evals/rag/stats.py`, `evals/rag/run_rag_eval.py` |
| Replay of every eval | `evals/run_all_replay.py` (`make eval`) |

## Sample and labels

| Item | Value |
|---|---|
| Sampled questions | 30 of 60 dev (paraphrase 18, identifier 6, unanswerable 6), seed 26 |
| Labels | pending: written by the labelling agent after `generation_dev.json` exists |
| Label means (faithfulness, relevance, context precision) | pending live run |

## Judges

| Item | Value |
|---|---|
| Judge models (from E02, slugs verified at run time) | pending live run |
| Prompts committed after the labels (commit ids) | pending live run |

## Agreement with the labels (Cohen's kappa, bootstrap 95 percent CI, 2000 resamples, seed 0)

| Judge | Metric | Kappa | 95% CI | Raw agreement | Validated (kappa >= 0.60, CI low >= 0.30) |
|---|---|---|---|---|---|
| judge A | faithfulness | pending live run | pending live run | pending live run | pending live run |
| judge A | answer relevance (weighted) | pending live run | pending live run | pending live run | pending live run |
| judge A | context precision | pending live run | pending live run | pending live run | pending live run |
| judge B | faithfulness | pending live run | pending live run | pending live run | pending live run |
| judge B | answer relevance (weighted) | pending live run | pending live run | pending live run | pending live run |
| judge B | context precision | pending live run | pending live run | pending live run | pending live run |
| A vs B | all three | pending live run | pending live run | pending live run | not applicable |

## MILO scores (dev, sampled 30)

| Metric | Label mean | Judge A | Judge B |
|---|---|---|---|
| faithfulness | pending live run | pending live run | pending live run |
| answer relevance (0 to 2) | pending live run | pending live run | pending live run |
| context precision | pending live run | pending live run | pending live run |

Decision under the protocol's rule: pending live run. The hard claim "RAG evaluation" (D27) stands only if one judge
is usable (all three metrics validated, at most 5 percent unparsed replies).

## Offline checks

`make eval` replays tools, attacks, retrieval, ab, chat and rag. `make eval-rag` replays `evals/results/m7/*.json`
and regenerates the dry result from its fixture. The labels validator rejects a malformed file
(`tests/test_rag_eval.py`). The live runners refuse without `MILO_LIVE=1`, `--max-cost` and `--reason`. The judge
runner also refuses while a prompt is a placeholder, while the labels are missing or invalid, or while git does not
show the labels committed before the prompts.

## Spend

Estimate 0.44 USD, cap 0.90 USD (generation 0.40, judge 0.50). Measured: pending live run.

## Limits

See `docs/reflection.md` and the limits section of `evals/protocols/m7-judge.md`.
