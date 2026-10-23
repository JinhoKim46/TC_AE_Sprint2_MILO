# Protocol C1-BASELINE: Haiku baseline on the data-v2 dev set, and judge validation on the label pool

| Field | Value |
|---|---|
| Protocol id | `c1-baseline` |
| Status | committed before results |
| Date | 2026-10-23 |
| Implements | SPEC D24, D26, D83, D126, D159, D161 to D169; HARNESS rules 6, 7, 14; data-v2 section "Data 3" (the judge run on the pool) |
| Produces | `evals/results/agent/tools_c1.json`, `evals/results/discovery/discovery_c1.json`, `evals/results/m7/generation_dev_c1.json`, `evals/rag/labels_c1.jsonl`, `evals/results/m7/judge_validation_c1.json` |
| Estimate / cap | about 0.50 USD in total, cap 1.00 USD (per run below) |

## Question

What does MILO score on the new dev data when every model call goes to the cheapest working model (D169), and is
there an LLM judge whose labels agree with the audited ones well enough to score later improvement arms?

This is a new baseline. The earlier M3, M5b and M7 numbers were measured on other data and on Sonnet; they are not
compared with these. Later C3 arms are compared with this baseline, on the same data and the same model.

## Models

| Role | Model | Price USD per M tokens (in / out, catalogue 2026-10-23) |
|---|---|---|
| Agent, extraction, reply, generation | `anthropic/claude-haiku-5.5` | 0.10 / 0.50 |
| Judge 1 | `anthropic/claude-haiku-5.5` | 0.10 / 0.50 |
| Judge 2 | `deepseek/deepseek-v4-flash` | 0.091 / 0.182 |
| Embeddings | `google/gemini-embedding-2` (index from kb-edit-v1) | |
| Input gate | `typesafe/jev-1.14-20261014` (where the pipeline calls it) | |

Reasoning (D159): off unless a call returns the typed `ReasoningRequired` error; then the model joins
`REASONING_REQUIRED` and that one run is started again from the beginning, with the restart noted in the ledger.

## Runs, in order

1. **Tools** (`run_tools.py`, 21 dev cases, cap 0.05 USD, output `tools_c1.json`). The runner's own metrics:
   tool choice, arguments, accepted outcomes. **Viability gate:** if fewer than half of the cases score a correct
   tool choice, stop after this run and report to the owner before spending on the others, because the D169
   choice would then need a decision.
2. **Discovery** (`run_discovery.py`, the 11 dev conversations in `splits.json`, scorer v2, cap 0.30 USD, output
   `discovery_c1.json`). Metrics: fact F1 against the audited expected files, early capture (D162), premature
   estimate, the injection check on protected fields (D167), conflict handling.
3. **Generation** (`run_rag_eval.py --generate`, the 30 qids of `evals/rag/sample_dev.json`, cap 0.05 USD, output
   `generation_dev_c1.json`). Same pipeline as M7: `build_app`, contract mode, dense top 5, `min_score` 0.30.
4. **Labels** (no model spend). One labelling agent on the subscription labels the 30 Haiku answers with the
   `evals/rag/schema.py` rubric into `labels_c1.jsonl`. It sees questions, contexts and answers only: not the gold
   reference answers' scores, not the judge prompts, not any judge output, and not which model answered. The
   labels are committed before run 5.
5. **Judge validation** (`run_rag_eval.py --validate-judges`, cap 0.60 USD, output `judge_validation_c1.json`).
   The two judges above score three labelled sets with the frozen prompts `evals/rag/prompts/*.v1.md` (no prompt
   edit from any C1 result):

   | Set | Items | Labels |
   |---|---|---|
   | `real-m7` | 30 Sonnet answers of M7 (`generation_dev.json`) | `evals/rag/labels.jsonl` |
   | `real-c1` | 30 Haiku answers of run 3 | `labels_c1.jsonl` |
   | `pool` | 90 pool items (`data/judge_pool/`) | the data-v2 auditor's blind labels |

   The `real-m7` labels judge each answer against the contexts stored with it, so they stay valid after the KB
   edit. Pool rows never enter a product number.

## Decision rules (fixed here)

- **Judge usable:** the m7-judge rule (kappa at least 0.60 and CI lower bound at least 0.30 for faithfulness,
  relevance and context precision, unparsed replies within the runner's limit), applied to the **combined** set of
  150 items. Per-set kappas and prevalence are reported beside it. When both judges are usable, the one with the
  higher kappa sum is the headline judge (the runner's rule).
- **Product RAG numbers** for the baseline are the label means on `real-c1` (faithfulness, relevance, context
  precision) and the abstention count on its 6 unanswerable questions, not a judge score. The headline judge's
  numbers on `real-c1` are reported beside them.
- Tools and discovery numbers are reported as measured; nothing is tuned on them in C1.

## Known limits

- 30 generation questions and 11 conversations give wide intervals; C1 is a reference point, not a verdict.
- The labels on `real-c1` come from one labelling agent; the auditor of the pool is a different session.
- The judges include the agent's own model (Haiku judging Haiku answers on `real-c1`); its kappa on that set is
  reported separately for that reason.
