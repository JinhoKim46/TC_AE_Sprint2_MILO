# C1 report: Haiku baseline on the data-v2 dev set

Protocol `evals/protocols/c1-baseline.md` (committed before results). Date 2026-10-23. Every model call goes to
`anthropic/claude-haiku-5.5` (D169), with the judges named below. Total OpenRouter spend 0.197 USD. This is a new
baseline: earlier M3, M5b and M7 numbers used other data and Sonnet, and are not compared with these.

## Results

| Part | Metric | Value |
|---|---|---|
| Tools (21 dev cases) | case pass rate | 0.571 [0.333, 0.762] (12 of 21) |
| | correct tool name | 0.571; false call rate 0.0; missed call rate 0.118 |
| | exact arguments on exact cases | 0.667 |
| Discovery (11 conversations, 201 turns, scorer v2) | fact F1 | 0.837 (precision 0.851, recall 0.823) |
| | state accuracy | 0.751 [0.697, 0.811] |
| | next-question accuracy | 0.443 [0.378, 0.512] |
| | premature estimate | 0.0 |
| | injections held (protected fields unchanged) | 7 of 7 |
| | conflicts raised | 1 of 6 (only D-3) |
| | evidence quoted verbatim | 1.0 (14 facts discarded for non-verbatim evidence) |
| RAG answers (30 sample questions, blind labels) | faithfulness | 0.80 (6 unfaithful) |
| | relevance (0 to 2) | 1.80 |
| | context precision | 0.30 |
| | abstention on 6 unanswerable | 0 empty-context abstentions; all 6 declined in the text (relevance 1, faithful) |

## Judge validation (150 labelled items)

| Set | n | unfaithful | Haiku judge kappa F / R / CP | DeepSeek v4 flash kappa F / R / CP |
|---|---|---|---|---|
| real-m7 (Sonnet answers) | 30 | 1 | 0.20 / 0.81 / 0.77 | 0.24 / 0.79 / 0.73 |
| real-c1 (Haiku answers) | 30 | 6 | 0.52 / 0.90 / 0.66 | 0.22 / 1.00 / 0.67 |
| pool (perturbed answers) | 90 | 40 | 0.76 / 0.90 / 0.70 | 0.82 / 0.76 / 0.77 |
| **combined (deciding set)** | 150 | 47 | **0.70 / 0.89 / 0.71** | **0.70 / 0.79 / 0.74** |

By the pre-set rule both judges are usable on the combined set; the headline judge is Haiku (higher kappa sum).
Unparsed replies: Haiku 0.2 percent, DeepSeek 2 percent. Judge numbers on `real-c1`: Haiku judge faithfulness
0.87, relevance 1.77, context precision 0.31 (labels 0.80, 1.80, 0.30).

**Limit that matters:** the judges are validated mostly by the pool. On real answers alone faithfulness kappa is
weak (0.52 with a CI reaching 0 on Haiku answers), because real errors are subtle (a fee called "one-time" when
no context says so) while pool perturbations are clearer. The Haiku judge also scores Haiku's own answers more
faithful than the labels do (0.87 against 0.80). Use the judge to compare arms; confirm a faithfulness
improvement with blind labels before adopting it.

## Where the misses are (inputs to C3)

- **Tools.** 5 of 9 failures are extra calls around a right one (looking up the session spec before an estimate,
  searching twice); 2 are missed withdrawals (tc07, tc17); 2 are pain statements not recorded as facts (tc15,
  tc16).
- **Discovery.** Conflicting figures are rarely raised (1 of 6), and the next question often differs from the
  protocol's order (0.44). Extraction itself is solid (F1 0.84).
- **RAG.** Unfaithful answers add small unsupported details; only about 1.5 of 5 retrieved chunks are needed per
  question (context precision 0.30). Retrieval never returns an empty context (D160), so abstention is the
  model's text, not the pipeline's.

## Files

`evals/results/agent/tools_c1.json`, `evals/results/discovery/discovery_c1.json`,
`evals/results/m7/generation_dev_c1.json`, `evals/rag/labels_c1.jsonl`, `evals/results/m7/judge_validation_c1.json`.
All replay offline.
