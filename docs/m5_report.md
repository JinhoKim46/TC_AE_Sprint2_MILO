# M5b report: protocol, facts, chain, turns, approvals, prompts

Status: built on `agent/m5b`; verified, owner review pending (the verifier writes the verdict). Offline only: no
model or network call was made (D126). The live discovery run is in the integrator's live batch.

## What was built

| Path | What it does |
|---|---|
| `src/milo/protocol/loader.py` | loads `protocols/discovery.v1.yaml` once, path resolved from the repo root |
| `src/milo/protocol/engine.py` | stateless over the facts: state (`collecting`, `assessment_ready`, `quote_ready`, `quote_blocked`), estimate status (D39), `number_allowed`, assumptions, missing fields, next question (conflicts first, text from the YAML) |
| `src/milo/protocol/validators.py` | type, range, unit and period checks with normalisation; cross-field rules (a conditional field refused when its condition is known false; e-mail shape; re-keying minutes against handling minutes) |
| `src/milo/protocol/replycheck.py` | D108 reply check in code: money amounts removed, scope rule, next question appended |
| `src/milo/facts/model.py`, `merge.py`, `store.py` | Pydantic extraction (per-item validation, `asks_about` enum); the merge (D134 verbatim evidence, D22 pricing refusal at taint high, `unknown`, `reported`, `conflicting` with both values kept, `confirmed` only by code with the period converted); `SessionFacts`, the single merge path for the chain and for `record_fact`, and the `SpecStore` that `MiloBackend` reads |
| `src/milo/prompts/{extract,reply,followup}.md`, `discovery.py` | prompts as files; owner text, documents and excerpts in marked data blocks; no figure ever enters a prompt |
| `src/milo/chain.py` | the LCEL sequence `policy -> extract -> merge -> protocol -> retrieve -> reply -> estimate`; at most four model calls; events to `on_event` and `turn_events`; traces to the tracer and the `traces` table (hashes, lengths, counts, cost only); approvals over the `approvals` table through `ApprovalService`, with the session's stored taint at approve time |
| `src/milo/turns.py` | `TurnRunner`: consent gate (D135), validation, per-account limits (D131: messages per minute, daily spend, one running turn per account), uploads capped (D132), background run, `events(after=seq)` polling |
| `src/milo/app.py`, discovery branch only | `App.ask` returns the discovery turn; `App.resume_approval` routes to the chain in discovery mode; `build_discovery_app` builds `build_app(..., discovery=True)` over the in-memory store. Contract mode is unchanged (all seven kit targets pass) |
| `evals/discovery/run_discovery.py`, `dry_script.json` | dry, replay and live modes over A-1, B-1, E-1 (protocol `evals/protocols/m5b-discovery.md`, committed first) |
| `docs/traces/B-1-dry-conflict-and-upload.jsonl` | one demo-company trace (B-1 turns 6, 8, 9, 22): events and trace rows of the conflict, its confirmation and the injected upload |

## Done-line

| Check | Result |
|---|---|
| `uv run pytest tests/test_protocol.py tests/test_turns.py` | 35 + 10 passed |
| dry discovery evaluation, premature-estimate rate | 0.0 over 41 turns where the chain allows no figure (and 0.0 over the 41 where the expected file says so); `evals/results/discovery/discovery_dry.json` |
| `make eval-discovery` (replay) | matches |
| `make discovery-table` | runs; outcome below, row by row against SPEC section 14 |
| `make kit-threads` (contract mode) and every `kit-*` target | all pass (9 tests under 7 keywords) |
| `make check` | ruff, format, mypy, `uv lock --check`, data checks and size check pass; pytest 550 passed, 1 failed: `tests/test_s0_smoke.py::test_discovery_and_create_app_are_stubs_until_their_milestones` asserts that `build_discovery_app` still raises `NotImplementedError`. That S0 file is integrator-owned; the change is requested in `docs/requests/m5b.md` |
| replays | `eval-tools-replay`, `eval-attacks-replay`, `eval-retrieval-replay`, `eval-chat-replay`, `eval-discovery` all match |

## Dry evaluation (scripted model; checks the chain and the gates, not a model)

Pooled over 54 dev turns: fact precision 1.0, recall 1.0, F1 1.0; status accuracy 1.0; state, next-question and
estimate-status accuracy 1.0; premature-estimate rate 0.0; evidence verbatim rate 1.0; repeated-question rate 0.0;
model calls per turn 2.0 (max 2); cost 0. Conflict handling: B-1 turn 8 raises the conflict (`quote_blocked`, next
question `confirm_daily_request_volume`, no figure) and turn 9 confirms 19 per business day in code (400 / 21,
rounded). Injection handling: E-1 turn 4 and B-1 turn 22 (the upload's injected line) are taint high, the scripted
pricing fact is refused (`policy_denied`), nothing is confirmed and no stored status changes. The scripted reply
quotes a price whenever the owner mentions one (7 turns); the reply check removed every amount.

The dry extractor is written from the same protocol, so these 1.0 values say the code is right, not that a model
extracts well. Token counts from the dry trace (about 1,450 input tokens per extract call and 780 per reply call,
before tool specs) feed the live budget.

## `make discovery-table` against SPEC section 14

Predicted: extract consumes the first scripted reply (a tool-call reply has empty content, so parsing fails), the
JSON retry consumes the second, the turn ends with the "repeat the last point" reply and nothing executes.

| Keyword | Kit test | Predicted | Observed | Reason observed |
|---|---|---|---|---|
| sources | `test_sources_present_for_kb_answer` | fail | fail | extract consumed "Every six months." (not JSON), the re-ask got "ok"; repeat reply, no retrieval, `sources == []` |
| abstains | `test_abstains_without_model_call_on_empty_retrieval` | fail | fail | discovery always makes the extract call: `chat.calls == 2`, not 0 |
| tools | `test_tools_tool_result_shown_with_policy_decision` | fail | fail | the tool-call reply was consumed by extract; `tool_results == []` |
| security | `test_security_destructive_tool_needs_approval` | fail | fail | same; no tool result, `stopped` false; nothing ran |
| security | `test_security_identity_not_from_arguments` | pass (vacuous) | pass (vacuous) | `backend.executed` stays empty. The non-vacuous check is in `tests/test_turns.py::test_kit_style_ask_binds_identity_and_keeps_one_session_per_user` (backend receives `u1`, not `u2`) |
| errors | `test_errors_invalid_input_rejected_with_typed_error` | pass | pass | validation runs before the chain; no call |
| errors | `test_errors_provider_error_becomes_user_message_not_traceback` | pass | pass | the extract call fails three times inside `llm/retry.py`: `provider_error:unavailable` / `:timeout` |
| threads | `test_threads_isolated` | fail | fail | one session per user (D2): `t2` maps to the same session, so the extract prompt for `t2` holds `ZEBRA-7731` |
| cost | `test_cost_reported` | fail | fail | two calls were made (extract and re-ask): 0.002 + 0.00002 USD, not 0.002 |

All nine rows match the prediction. Raw output: `make discovery-table` (exits 0).

## Decisions (with SPEC references)

1. **One session per user in `build_discovery_app`** (D2 wording). A `thread_id` that is one of the owner's session
   ids opens that session (web, D97); any other `thread_id` maps to the owner's default session. That is why the
   kit `threads` row fails as predicted.
2. **Caller ids to store ids.** The kit uses `u1`, the dev data `owner-a`; the store needs uuids (D72). A uuid stays
   itself; anything else maps to a stable uuid5. The backend always receives the caller's own id (D21).
3. **Confirmation** (D37): the field must be the open conflict question at the start of the turn (the engine's next
   question), and the owner gives a value; the volume is converted to the canonical period in code and rounded to
   whole requests. A value given outside that question adds to the kept values and stays `conflicting`.
4. **Same statement, not a conflict:** two volumes within 15 percent per business day (35 a day and 700 a month)
   are one statement; two strings where one contains the other are one statement.
5. **Taint is sticky per session** (D22, D131 "stored taint"): high once any owner message, upload, retrieved text
   or tool output of the session was. The merge applies the D22 pricing refusal on the extract path too, and refuses
   confirmations at taint high. The approve-time policy (`decide_on_approve`, m4 request) gets this taint. The loop
   classifies its own texts, so a sticky-high session passes a fixed marker phrase into it.
6. **Money in replies is always removed** (D108, section 5 step 7): the model never sees the figures, so any amount
   it writes is invented. This makes the premature-estimate rate 0 by code, whatever the model writes.
7. **Estimate modules:** the model's `estimate_cost` module ids when it called the tool, else a keyword match over
   goals and channels (provisional; M5c owns the recommendation). Blocked turns emit a status-only
   `estimate_updated`; `collecting` emits none.
8. **Approvals over the table:** the `approvals` table stores the argument hash, so the arguments and the saved
   messages are kept in process; a restart loses them and the hash check then runs nothing (fail closed).
   `denied_at_approve` is stored as `kept` (or `expired` once claimed), the statuses the table allows.
9. **Per-account limits** (D131): messages per minute and uploads per hour through `RateLimiter`; the daily spend is
   counted in process per account (a persistent counter is requested); one running turn per account comes from the
   turns repository.
10. **The evaluation's retrieval** uses the hashed embedder at `min_score` 0.10, so knowledge replies in the eval are
    a lower bound; M1 measures retrieval.

## Live run (integrator's live batch; not run here)

```
MILO_LIVE=1 uv run python evals/discovery/run_discovery.py --model <claude-sonnet-5.5 catalogue slug> \
  --price-in <usd per M> --price-out <usd per M> --max-cost 1.50 \
  --reason "product model extraction through the discovery chain is the measurement"
uv run python evals/discovery/run_discovery.py --replay --file evals/results/discovery/discovery_live.json
```

Estimate: 54 turns, about 2.2 calls per turn (about 120 calls), about 2,900 input and 250 output tokens per turn.
At 3 / 15 USD per million tokens (to verify from the catalogue) that is about 0.012 to 0.015 USD per turn, 0.65 to
0.80 USD in total; capped at 1.50 USD.

## Limits

- Three dev conversations; the dry run cannot find extraction-prompt problems, only the live run can.
- Evidence quotes are not stored in the `facts` or `evidence` tables (the table holds a hash); the quote is in the
  `fact_recorded` event and in process. A column is requested.
- `App.recent_traces` (integrator code) still reads the in-memory tracer in discovery mode; the chain also writes the
  `traces` table.
