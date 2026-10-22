# F2 final run plan: the single, sealed test-split run

Committed before any test-split read or spend (D83). Implements SPEC 10.1, 10.5, section 17 (F2), HARNESS rule 6.
Every run below reads the test split once, with the code, prompts, thresholds and settings frozen at dev. Nothing is
tuned after a result is seen; a miss is reported as measured.

## Rules

- Gold check first: `common.gold_sha256()` must equal the `data/gold.jsonl` entry of `data/MANIFEST.json`, else
  F2 stops before any read.
- Only `make final-test` sets `MILO_FINAL=1` (the M5G and M5b test steps are started by `run_final.py` with that
  environment). `run_final.py` also needs `MILO_LIVE=1`, `--max-cost` per step is fixed in the script from this plan.
- One attempt. `evals/results/test_access.log` gets a `started` line per test load (from `common.load_gold`), a
  `failed <step>` line if a step raises, and exactly one `ok` line, written only when every step below has a result.
- Each step refuses when its result file exists, so no step reads the test split twice. A step may be retried once
  only if it failed with a provider error before writing its result (m5g-gate.md run 4 wording); that retry goes
  into the ledger as its own row.
- The labelling (step 7) is session work between M7 generation and the M7 judge, so `make final-test` is invoked
  twice: the first invocation runs steps 1 to 6 and writes `awaiting-labels` to the log; the labels are written by a
  separate labelling agent and committed; the second invocation finds steps 1 to 6 done, runs step 8, replays every
  test result and writes `ok`.
- Spend: before each step, actual cumulative F2 spend plus the step's cap must be at most **2.50 USD**, else the
  step is skipped and reported. `MILO_BUDGET_USD` is set to the real remaining balance (8.47 minus the 1.1971 USD of
  the live-batch ledger, rounded down to 7.27). The key is sourced from the AE `.env` inside the command only.
- Code changes before the run (committed with this plan's follow-up commit, before any test read): a `split`
  argument (default `dev`) and output path on the M1, E02, M7 and M5b runners, `evals/final/run_final.py`, and the
  test replays in `make eval`. Scoring, prompts, thresholds, models and settings are not touched; every dev replay
  must still match afterwards.

## Runs, in order

| # | Run | Protocol clause | Command (inside `make final-test`) | Model | Cap USD | Estimate USD | Result file |
|---|---|---|---|---|---|---|---|
| 1 | M1 dense retrieval, 60 test rows (also rebuilds the git-ignored KB index, 167 chunks, 800/100) | e01-rerun.md "F2 reports the test numbers for the chosen model once"; SPEC D5 | `run_final.py` step `m1` (reuses `run_retrieval.py` with `split=test`) | `google/gemini-embedding-2` | 0.01 | 0.003 | `evals/results/retrieval/retrieval_test.json` |
| 2 | M2 on test | m2-ab.md "single test-split run at F2 for the adopted configuration only"; no arm was adopted on dev (`ab_dev.json`), so the adopted configuration is dense, which is run 1 | none (no extra call); `final_report.md` compares dense test with dense dev and lists the dev arm table for reference | - | 0 | 0 | run 1 |
| 3 | M5G gate, test scope rows (about 210) | m5g-gate.md run 4 "single test-split run" | `MILO_FINAL=1 MILO_LIVE=1 uv run python evals/gate/run_gate.py --final --max-cost 0.05 --reason "stage-2 probability needed to set or confirm a threshold"` | `typesafe/jev-1.14-20261014` | 0.05 | 0.01 | `evals/results/m5g/gate_test.json` |
| 4 | E02 step 7: task A on the 48 test turns (A-2, B-2, E-2) and task B on the 60 test gold rows; chosen model and reference only | e02-chat-compare.md step 7 | `run_final.py` step `e02` (reuses `compare_chat.py`, models pinned from the catalogue) | `anthropic/claude-haiku-5.5`, `openai/gpt-4.1-mini` (reference); embeddings `google/gemini-embedding-2` | 0.30 | 0.12 | `evals/results/e02/chat_test.json` |
| 5 | M7 generation: 30 test questions (stratified 18 paraphrase, 6 identifier, 6 unanswerable, `random.Random(26)`, the dev sampler's rule), product pipeline, contract mode, dense top 5, `min_score` 0.30 | SPEC 10.5 "F2 reruns the same script on test"; m7-judge.md Limits | `run_final.py` step `m7-generate` (reuses `run_rag_eval.py` generation) | `anthropic/claude-sonnet-5.5`, reasoning low effort (D159), 2.00 / 10.00 per M | 0.40 | 0.24 | `evals/rag/sample_test.json`, `evals/results/m7/generation_test.json` |
| 6 | M5b discovery: test conversations A-2, B-2, E-2 (48 turns) | m5b-discovery.md "the test conversations are run once in F2" | `MILO_FINAL=1 MILO_LIVE=1 uv run python evals/discovery/run_discovery.py --final --model anthropic/claude-sonnet-5.5 --price-in 2.0 --price-out 10.0 --max-cost 1.00 --reason ...` | `anthropic/claude-sonnet-5.5`, reasoning low effort (D159) | 1.00 | 0.54 | `evals/results/discovery/discovery_test.json` |
| 7 | M7 labels for the 30 test answers | m7-judge.md Data 3 (separate labelling agent, D26) | session agent; reads only the packet `run_final.py` writes (`evals/results/m7/label_packet_test.json`: question, reference answer and quotes, contexts, answer); writes `evals/rag/labels_test.jsonl`, committed alone before step 8 | session (no OpenRouter call) | 0 | 0 | `evals/rag/labels_test.jsonl` |
| 8 | M7 judge on the 30 test answers, the two dev judges, the dev prompts unchanged (hashes must equal `judge_dev.json`'s) | SPEC 10.5; m7-judge.md steps 5 and 6 | `run_final.py` step `m7-judge` (second invocation) | `anthropic/claude-haiku-5.5` 0.10 / 0.50, `openai/gpt-4.1-mini` 0.40 / 1.60 | 0.10 | 0.04 | `evals/results/m7/judge_test.json` |
| | **Total** | | | | **1.86** | **0.95** | |

Caps sum to 1.86 USD, below the 2.50 USD F2 cap. Estimates come from the dev actuals in `docs/live-batch-ledger.md`
(E02 haiku plus gpt-4.1-mini task A 0.100 for 54 turns and task B 0.022 for 60 rows; M7 generation 0.235 for 30;
M5b 0.602 for 54 turns; M5G 4.4e-05 per Jev call; M7 judge 0.038).

## Protocols with no test-split run

- M3 tools (`m3-tools.md`): the 15 tool cases have no split; nothing to run at F2.
- M4 attacks (`m4-attacks.md`): "There is no dev or test split"; nothing to run.
- SR live (`sr-live.md`): no test split; nothing to run.
- M0b: data only.

## Targets read from the protocols (stated before the run)

- M5G: zero in-scope test rows refused; at least 95 percent of out-of-scope test rows refused (both stages).
- M5b: premature-estimate rate 0 (code-gated); fact precision and recall, state accuracy and repeated-question rate
  reported against dev.
- E02: if the chosen model's test F1 falls more than 0.10 below the reference's, that is reported and the reference
  becomes the development model; nothing is re-tuned.
- M7: per judge and metric, kappa at least 0.60 and CI lower bound at least 0.30 (validated); usable when all three
  are validated and at most 5 percent of replies are unparsed.
- M1 and M2: no numeric target; test nDCG@5, recall@5 and MRR are reported next to dev, and D5's choice is not
  revisited.

## Replay

`make eval` replays every test result except `gate_test.json`, whose replay re-reads the sealed test scope texts and
therefore runs only inside `make final-test` (the second invocation replays every test file, the gate included).
