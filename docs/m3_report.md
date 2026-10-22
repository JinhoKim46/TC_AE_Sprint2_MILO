# M3 report: tools and pricing

Status: built on `agent/m3` from S0 (`f42d8f1`); verification and owner review pending. No model, network or
`.env` was used (SPEC D126, HARNESS rule 14).

## What is built

- `src/milo/tools/`: `specs.py` (five `ToolSpec`s, every schema `additionalProperties: false`), `backend.py`
  (`MiloBackend`: `specs()`, `call()`, `estimate_for()`, `pricing_fields`, an `executed` log with
  `state_changed` for the attack runner's `backend_state_changed` column, D65), `state.py` (`SpecStore`
  protocol, `MemorySpecStore`, `check_fact`), `args.py` (identity stripping with look-alikes),
  `approvals.py` (`ApprovalService`: the no-model approval lifecycle and the `/withdraw` path), `objects.json`.
- `src/milo/pricing/rate_card.py`, `estimator.py`: tiers, surcharges, setup fee, range, status gating.
- `src/milo/loop.py`: the S0 body kept in shape, with four changes: look-alike identity stripping before schema
  validation, the backend's `pricing_fields` passed to the policy (S0 passed none, so a pricing `record_fact`
  at taint high was not denied), a non-object `args` becomes `invalid_arguments`, a policy fault fails closed.
  Tool output cap raised from 2000 to 6000 characters so five 800-character chunks stay valid JSON.
- `evals/agent/run_tools.py` (`--dry`, `--replay`, live behind `MILO_LIVE=1`), `evals/results/agent/tools_dry.json`.

## Decisions

- Estimate formula follows the KB articles: monthly = sum(base x tier factor) + one surcharge per distinct
  integration; setup = 900 x modules; low/base/high apply 0.90/1.00/1.20 to the monthly and the setup totals
  separately (SPEC 5.2, D38). Tier bounds are inclusive. Lines round to cents, half up.
- Status set is D39 plus `unavailable` (assessment fields incomplete, or volume above 150 a day): the protocol
  header says `collecting` shows no number. `blocked` when a `blocking` field is `conflicting`.
- `estimate_cost` output is `{status, module_ids, assumptions}`; assumptions are codes (`needs:`, `conflict:`,
  `not_reported:`, `estimated:`, `integration_assumed:`), never figures. The chain gets figures from
  `MiloBackend.estimate_for` (`Estimate.event_payload()`), per SPEC 5 step 7.
- A missing or empty integration list assumes `csv-export` (surcharge 0) and makes a scenario (KB: setup fees).
- Weekly and monthly volume convert with `conversions` in the protocol (5 and 21 business days).
- `withdraw_submission` returns the same `not_found` for another owner's id and a missing id (D65).
- `MemorySpecStore.put_fact` is a stand-in for the chain's merge (step 3): list fields accumulate, a differing
  scalar becomes `conflicting` with the old value kept, `confirmed` is never set. M5b swaps in its own `SpecStore`.
- `objects.json`: sessions `1` to owner-a; submissions `1` owner-a, `2` owner-b, `3` owner-c (absent elsewhere).
- Eval scoring: first model reply only; `exact` compares names and args, `names` compares the multiset of
  names; bootstrap CI 2000 resamples, seed 0 (D24). Knowledge search returns no chunks in the eval.

## Open items

- `data/tool_cases.jsonl` is still stale (M0 scope, SPEC 6 and 17): tc04 and tc13 carry `daily_request_volume`,
  which the `estimate_cost` schema rejects (the dry result lists them in `expected_schema_invalid_cases`), and
  tc07 asks owner-b to withdraw submission 1 (owner-a's). After M0 merges, rerun `--dry` to refresh the stored file.
- The rate card has no `automated_minutes_per_request` or `eligible_share` yet (M0, section 5.2); not needed here.
- Range factors applied to the one-time setup fee as well as the monthly total: SPEC says only "the base
  estimate". M5c may report monthly only. The `submissions.cost_*` columns hold one figure; M5c chooses.
- The live run needs a protocol in `evals/protocols/` first (D83) and an E02 model choice (D148); both are the
  integrator's. `App.resume_approval` still calls `tools.call` directly: it is safe because `call` validates and
  binds identity itself, but it does not re-run the policy. M4 may want that.
- Look-alike stripping lives in `tools/args.py`; M4 may fold it into `security/identity.py`.

## Provenance

`evals/results/agent/tools_dry.json` carries the D56 fields. It is a harness check (scripted model), not a
model measurement: pass rate 1.0 only shows that the loop, schemas and policy accept the expected calls.
