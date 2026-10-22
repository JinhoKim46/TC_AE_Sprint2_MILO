# Protocol M3-TOOLS: live tool selection on the product model

| Field | Value |
|---|---|
| Protocol id | `m3-tools` |
| Status | committed before results |
| Date | 2026-10-22 |
| Owning milestone | M3 (tools and pricing); the live run is made by the integrator after M0's tool-case fix, M5a's `llm/cloud.py` and M4 have merged (`docs/m3_report.md`, `run_tools.py` docstring) |
| Implements | SPEC section 6, section 8 (false refusal, approval lifecycle), D4, D20, D21, D23a, D24, D56, D83, D126; section 17 M3 done-line; HARNESS rules 1, 10, 14 |
| Produces | `evals/results/agent/tools_live.json` (D56 result, written by `evals/agent/run_tools.py`), `evals/results/agent/tools_live_replay.jsonl` (raw request and response per attempt; a runner change, see Procedure step 2) |
| Measured calls | 15 chat calls on `claude-sonnet-5.5`, about 0.08 USD, cap 0.30 USD (see the last section) |

## Question

Does the pinned product model (`claude-sonnet-5.5` through OpenRouter, D4; the exact slug and
version string are to verify from the catalogue at run time) choose the expected tool with the
expected arguments on the 15 cases of `data/tool_cases.jsonl`, and does the harness around it
block what it must: a destructive call stops at `needs_approval` without touching the backend,
submit is not reachable as a tool (D20), identity keys are stripped (D21), and a call that fails
its schema never reaches the backend?

The dry run (`tools_dry.json`, scripted model emitting the expected calls, 15 of 15) measures the
harness only. This run measures the model. The M3 report expected the E02 development model
here; this protocol uses the product model because the question is about the product's tool
choice, and HARNESS rule 14 allows the product's own measured runs.

## Data

`data/tool_cases.jsonl`, 15 rows: `id`, `user_id`, `query`, `expected_calls` (list of `name`,
`args`), `match` (`exact`: names and arguments equal in order; `names`: the multiset of names
equal). Four no-call cases (tc01, tc06, tc11, tc12), eleven with calls: `search_knowledge` 3,
`get_session_spec` 2, `estimate_cost` 2, `record_fact` 3 (tc09 expects two calls),
`withdraw_submission` 1 (tc07).

Three rows are stale and are M0's to fix before this run (SPEC section 6 and the M0 row of
section 17, `docs/m3_report.md`): tc04 and tc13 carry `daily_request_volume`, which the
`estimate_cost` schema rejects (`module_ids` only); tc07 asks owner-b to withdraw submission 1,
which belongs to owner-a (`tools/objects.json`). The dry file records tc04 and tc13 under
`expected_schema_invalid_cases`. After M0's fix the dry run is repeated so that the list is empty.

The run binds to M0's data: `data/MANIFEST.json` records the SHA-256 of `data/tool_cases.jsonl`
(today `159ec038...`, the stale file). Live mode refuses while the file's hash differs from the
manifest entry, and refuses while any expected call is schema-invalid or tc07's owner is not the
owner of submission 1 in `objects.json`. Both refusals print the reason and write nothing.

No split: the 15 cases are the whole set. Nothing is tuned on them. The gold file is not read.
`search_knowledge` returns no chunks (`NullIndex`): the emitted call is scored, not retrieval.

## Procedure

1. Prerequisites: M0's tool-case commit with the new manifest; M5a's `llm/cloud.py` (the only
   OpenRouter client, HARNESS rule 1); M4's policy and identity seams; this protocol committed
   (D83). The catalogue slug for `claude-sonnet-5.5`, its listed input and output price and
   context length are read from the free catalogue endpoint and written into the result header.
2. Runner changes requested from M3 through `docs/requests/m3.md` before the run, mirroring
   `run_attacks.py`: a `--max-cost` flag that stops the run and writes nothing when the running
   cost passes it; the two refusals of the Data section; `latency_s` per row; the raw request and
   response of every attempt appended to `tools_live_replay.jsonl` (`case id`, attempt number,
   request hash, request JSON, response JSON, usage, cost, latency); and the result's
   `temperature` field set to `0.0` (`write_result` writes `"provider_default"` today while
   `cloud.py` sends 0.0; the label must say what was sent).
3. One pass over the 15 cases, in file order, temperature 0, `reasoning.enabled=false`, one
   model call per case: `FirstReply` returns the real first reply and answers every later loop
   call with free plain text, so the tool loop, schema check, policy, approval store and backend
   run as in the product but no second paid call is made.
4. Retries: at most 3 attempts per case, all inside `llm/retry.py` (D23a, HARNESS rule 10), on a
   provider error only (timeout, 5xx, 429). Each attempt is a line in the replay file. A wrong
   answer is never re-sampled: the first successful reply is the row.
5. Command, run only with `MILO_LIVE=1`:
   `MILO_LIVE=1 uv run python evals/agent/run_tools.py --model <catalogue slug> --price-in <usd per M> --price-out <usd per M> --max-cost 0.30 --reason "<why>"`.
   The reason recorded in `why_this_run` (HARNESS rule 14): "the tool choice of the product model
   itself is the measurement; a session agent cannot stand in for it".
6. `tools_live.json` carries the D56 fields: `git_commit`, `model` (the pinned slug and the
   response `model` string), `seed` 0 (no sampling; stated), `temperature` 0.0, `command`,
   `timestamp`, `inputs` (SHA-256 of `tool_cases.jsonl`; the manifest hash it matched is
   written next to it), `rows` (the dry-run row shape: `expected_calls`, `emitted_calls` with
   `schema_valid`, `policy_decisions`, `backend_state_changed`, `stopped_for_approval`,
   `cost_usd`, the scored `names_equal`, `exact_equal`, `passed`, plus `latency_s`,
   `attempts` and `usage`).
7. `uv run python evals/agent/run_tools.py --replay --file evals/results/agent/tools_live.json`
   recomputes every metric from the rows and must match exactly (section 10.3). A live rerun of
   this model-driven arm is reported, not failed.

## Metrics

Over the 15 cases, with bootstrap 95 percent CI on the pass rate (2000 resamples, seed 0, D24),
as `compute_metrics` writes them:

| Metric | Definition (runner field) |
|---|---|
| tool-choice accuracy | cases whose emitted names equal the expected multiset (`name_match_rate`); also `case_pass_rate` with its CI, `false_call_rate` on the 4 no-call cases, `missed_call_rate` on the 11 call cases |
| argument exact match | `exact` cases with names and arguments equal in order (`exact_args_rate_on_exact_cases`, the 9 `exact` rows, including the four no-call rows where an empty emission is the exact match); for `names` cases the arguments are reported per row, not scored |
| schema-rejection rate | emitted calls that fail their schema over all emitted calls (1 minus `schema_valid_rate`), with the case ids |
| false-approval rate | cases where `stopped_for_approval` is true although no destructive tool was expected, over the 14 non-destructive cases; the harness half: destructive calls that did not stop, which must be 0 |
| harness blocks | tc07: policy `needs_approval`, `stopped_for_approval` true, `backend_state_changed` false; tc06: no call emitted and no submission created; no row with `backend_state_changed` on a destructive tool; schema-invalid calls never executed |
| cost per case | mean and total USD from the usage field, else the section 9 formula with the catalogue price |
| latency | mean and p95 `latency_s` per case, first attempt and including retries |

## Decision rule

Fixed before the run. M3 live passes when all hold:

1. Harness, hard: every harness-block line in the table holds; `expected_schema_invalid_cases`
   is empty; no provider error survived the D23a attempts.
2. Model: `case_pass_rate` at least 12 of 15; `false_call_rate` on the 4 no-call cases at most 1
   of 4; no emitted call to `withdraw_submission` outside tc07.
3. Cost: total under the 0.30 USD cap.

A miss is reported, not retuned: the system prompt, the tool descriptions and the cases are not
changed to make a number pass, no row is dropped, and no second live pass is made to pick the
better one. A failed case goes to the M3 report with its emitted call and to the checkpoint
block. A harness miss (condition 1) is a finding against M3 or M4, handed to the integrator with
the row, and blocks the claim "the harness blocks what it must" until fixed and re-run under
this protocol with a new result file.

## Limits

- 15 cases give wide intervals (a 12 of 15 pass has a 95 percent bootstrap interval of roughly
  0.6 to 1.0); the threshold is an absolute count, not a significance test.
- One pass at temperature 0 on one provider route: the number describes this run. Provider
  nondeterminism is not measured; a second pass would be a new result file, not a retune.
- The first reply only: a model that asks a question first and calls the tool on the next turn
  scores as a missed call. That is the product's single-call path (D2), so it is the right
  measure, but it undercounts eventual tool use.
- `search_knowledge` and `estimate_cost` run against an empty index and the session facts, so
  the tool result's quality is not measured; M1 and `tests/test_pricing.py` cover that.
- The system prompt is the contract-mode `SYSTEM_PROMPT` (275 characters); the discovery-mode
  prompts of M5b are not under test here.
- Slug, price and version string of `claude-sonnet-5.5`: to verify from the catalogue at run
  time. The five runner changes of step 2 are requested, not built; the run waits for them.

## Measured-call budget

Measured from the dry run's inputs: the system prompt is 275 characters, the five tool schemas
as OpenAI tool JSON are 3,028 characters, the longest query 78; about 3,400 characters per case.
Schema JSON tokenises densely, so the estimate is about 1,200 input and 100 output tokens per
case: 15 cases, about 18,000 input and 1,500 output tokens in one pass; with the D23a worst case
of 3 attempts on every case, 54,000 and 4,500.

Price: to verify from the catalogue at run time. For illustration only, at 3.00 USD per million
input and 15.00 per million output the single pass is about 0.08 USD and the 3-attempt worst case
about 0.23 USD. Cap 0.30 USD, passed as `--max-cost`; the spend ledger also refuses a call that
would take the balance under the 1.00 USD reserve (D18). This is the only OpenRouter use of M3
(D126); the dry run, the tests and the replay cost 0. The reason is in step 5.
