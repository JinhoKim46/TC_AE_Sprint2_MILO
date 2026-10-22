# M7 report: RAG evaluation with an LLM judge

Status: complete on dev. Generation (7b05236), labels by the separate labelling agent (d6829c3), judge prompts
(afb8cb2, after the labels; the runner's D26 git check passes) and the live judge run (8087e76) are committed.
**No judge is usable under the protocol's rule**, so the hard claim "RAG evaluation" (D27) is not met; the label
means are the labelled-sample result.

## What was built

| Piece | File |
|---|---|
| Protocol (committed first, D83) | `evals/protocols/m7-judge.md` |
| Sampler: 30 dev qids, stratified by type, seed 26 | `evals/rag/sample.py`, `evals/rag/sample_dev.json` |
| Generation through injected callables | `evals/rag/generate.py` |
| Label schema, validator, template | `evals/rag/schema.py`, `evals/rag/labels.template.jsonl` |
| Judge prompts (written from the protocol's metric table, after the labels) | `evals/rag/prompts/*.v1.md` |
| Judge runner, agreement, order check | `evals/rag/judge.py`, `evals/rag/stats.py`, `evals/rag/run_rag_eval.py` |
| Results | `evals/results/m7/generation_dev.json`, `evals/results/m7/judge_dev.json` |
| Replay of every eval | `evals/run_all_replay.py` (`make eval`) |

## Sample and labels

| Item | Value |
|---|---|
| Sampled questions | 30 of 60 dev (paraphrase 18, identifier 6, unanswerable 6), seed 26 |
| Product | `anthropic/claude-sonnet-5.5`, contract mode, dense top 5, `min_score` 0.30, reasoning lowest effort (D159) |
| Labels | `evals/rag/labels.jsonl`, 30 rows by the labelling agent (d6829c3), schema check passes |
| Label means (faithfulness, relevance, context precision) | 0.9667, 1.80, 0.26 |

## Main finding (decision 79): no abstention on unanswerable questions

The product's `abstained` flag is False on all 30 rows, the 6 unanswerable questions included: every question
retrieved 5 contexts at `min_score` 0.30 (D160), so the product never took the no-context path. The labeller found
that all 6 unanswerable answers nevertheless **declined in the text** ("the knowledge base doesn't mention ...",
"I can't answer that from the knowledge base"), and scored them relevance 1 (honest decline, decision 81). Both
facts stand: by the flag MILO never abstains; by its words it declines all 6. Context precision on the unanswerable
stratum is 0.0 by the labels (none of the 30 retrieved contexts was needed), so the threshold lets irrelevant
contexts through and the model, not retrieval, does the declining. One of the 6 declines (q107) carries a claim the
labeller found unsupported, which is the only faithfulness 0 in the sample.

## Judges

| Item | Value |
|---|---|
| Judge models (E02, decision 68; slugs and prices checked in the catalogue on 2026-10-22) | `anthropic/claude-haiku-5.5` (0.10 / 0.50 USD per Mtok), `openai/gpt-4.1-mini` (0.40 / 1.60) |
| Prompts committed after the labels | labels d6829c3, prompts afb8cb2 (ancestor check passes) |
| Calls | 2 judges x 30 questions x 3 metrics = 180, temperature 0, reasoning off |
| Unparsed replies | haiku 0 of 90 (0.0), gpt-4.1-mini 0 of 90 (0.0); no provider error |

## Agreement with the labels (Cohen's kappa, bootstrap 95 percent CI, 2000 resamples, seed 0)

| Judge | Metric | Kappa | 95% CI | Raw agreement | Validated (kappa >= 0.60, CI low >= 0.30) |
|---|---|---|---|---|---|
| claude-haiku-5.5 | faithfulness | 0.20 | [0.00, 0.62] | 0.80 | no (low prevalence) |
| claude-haiku-5.5 | answer relevance (weighted) | 0.90 | [0.65, 1.00] | 0.97 | yes |
| claude-haiku-5.5 | context precision (150 contexts) | 0.75 | [0.64, 0.86] | 0.90 | yes |
| gpt-4.1-mini | faithfulness | 0.00 | [0.00, 0.00] | 0.97 | no (low prevalence; judge gave 1 to all 30) |
| gpt-4.1-mini | answer relevance (weighted) | 0.89 | [0.59, 1.00] | 0.97 | yes |
| gpt-4.1-mini | context precision (150 contexts) | 0.76 | [0.66, 0.85] | 0.91 | yes |
| haiku vs gpt-4.1-mini | faithfulness / relevance / context precision | 0.00 / 0.79 / 0.73 | [0.00, 0.00] / [0.44, 1.00] / [0.61, 0.82] | 0.77 / 0.93 / 0.89 | not applicable |

Faithfulness has one unfaithful label in 30 (minority below 5), so its kappa is flagged low prevalence and cannot
reach the thresholds: gpt-4.1-mini matches 29 of 30 by calling every answer faithful (kappa 0), and haiku finds the
one unfaithful answer but also marks 6 faithful ones unfaithful (kappa 0.20). For gpt-4.1-mini only 1268 of the 2000
resamples had a defined kappa.

## MILO scores (dev, sampled 30)

Under the decision rule no judge is usable (each fails faithfulness), so the judged numbers are **not validated** as
judge-level evidence. Relevance and context precision are validated as metrics for both judges.

| Metric | Label mean | claude-haiku-5.5 (not validated as a judge) | gpt-4.1-mini (not validated as a judge) |
|---|---|---|---|
| faithfulness | 0.97 | 0.77 (metric not validated) | 1.00 (metric not validated) |
| answer relevance (0 to 2) | 1.80 | 1.77 (metric validated) | 1.83 (metric validated) |
| context precision | 0.26 | 0.31 (metric validated) | 0.27 (metric validated) |

By type (label means): paraphrase 1.00 / 2.00 / 0.34, identifier 1.00 / 2.00 / 0.27, unanswerable 0.83 / 1.00 / 0.00
(faithfulness / relevance / context precision).

Decision under the protocol's rule: **no usable judge**; every kappa and CI is above; the hard claim "RAG evaluation"
(D27) is not met. The product's measured values on the labelled sample are the label means. If a later protocol
wants a usable judge, faithfulness needs a sample with more unfaithful answers, not a different prompt (the protocol
forbids changing a prompt after the first run).

## Caveats

- The prompts were written after the labels (D26). The prompt author did not open the labels before the run, but the
  protocol's caveat stands: a prompt written after the labels could be tuned to them, which would inflate kappa.
- One labeller (a model session), 30 questions: wide intervals, and agreement is with that labeller only.
- Runner defect for the record: the docstring and the stored `command` show a bare `--judge` mode flag, which argparse
  rejects because `--judge` takes `<slug>,<in>,<out>`. Judge mode is the default when no mode flag is given, and the
  run was made that way. Not fixed, no rerun.
- `tokens_in` and `tokens_out` in the generation rows are 0 (only cost is captured); the judge rows record tokens.

## Offline checks

`make eval` replays tools, attacks, retrieval, ab, chat, rag and discovery; `judge_dev.json` and `rag_dry.json`
recompute exactly from their rows and `labels.jsonl` matches the hash in the result. The labels validator rejects a
malformed file (`tests/test_rag_eval.py`). The live runners refuse without `MILO_LIVE=1`, `--max-cost` and
`--reason`, and the judge runner refuses while a prompt is a placeholder, while the labels are missing or invalid, or
while git does not show the labels committed before the prompts.

## Spend

Estimate 0.44 USD, cap 0.90 USD (generation 0.40, judge 0.50). Measured: generation 0.235042 USD (cap lowered to
0.30), judge 0.038281 USD (haiku 0.011292, gpt-4.1-mini 0.026989), M7 total 0.273323 USD. Ledger rows 11 and 13.

## Limits

See `docs/reflection.md` and the limits section of `evals/protocols/m7-judge.md`.
