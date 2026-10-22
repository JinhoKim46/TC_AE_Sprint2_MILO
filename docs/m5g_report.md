# M5G report: scope gate and Jev input gate

Protocol: `evals/protocols/m5g-gate.md` (committed before results). SPEC D103 to D118, D120, D128, D145, D147, D157,
D158. Status: **built and tested offline; the dev probe run and the single test run are pending** (live, integrator).
No number below is a measurement of Jev; the dry run uses synthetic probabilities and only checks the harness.

## What was built

| Path | What |
|---|---|
| `src/milo/gate/stage1.py` | Stage 1 rules 1 to 4 and 6 over `data/scope/lexicon.yaml` (v4): greeting list, strong term or two weak lemmas, approximate number plus unit, verb-object refuse patterns only when the text holds no lexicon term of either strength (fix cycle 1: a refuse pattern plus one weak term goes to stage 2; the simulation was fixed identically). The reference `data/scope/tools/stage1_sim.py` made into product code. |
| `src/milo/gate/jev.py` | Stage 2: one `noul` question (`in_diagnosis`) to `typesafe/jev-1.14-20261014`, rubric frozen from the protocol as `m5g-rubric.v1`. State is exactly `message`, `recent_turns` (last two, text only) and `scope_summary` (no `session_title`, D145). Tolerant response parser; any failure raises `JevFailure`. |
| `src/milo/gate/route.py` | Routing, the threshold rule (`compute_thresholds`), `T_REFUSE` / `T_PASS` loaded from `evals/results/m5g/gate_dev.json`, the fixed scope and greeting replies, `sr_gate` for the SR live runner. |
| `src/milo/llm/cloud.py` | Additive: `OPENROUTER_DECISIONS_URL` and `OpenRouterDecisions` (one POST of `{model, state, questions}`; HARNESS rule 1 keeps the URL here). |
| `src/milo/chain.py`, `src/milo/app.py` | Call site: a `gate` node between `policy` and `extract` in the discovery pipeline; `build_discovery_app(..., gate=None)`. Contract mode never builds the discovery runtime, so it never reaches the gate. |
| `evals/gate/run_gate.py` | `--dry`, `--replay`, `--live`, `--final`. |
| `tests/test_gate.py` | 39 offline tests. |

## Routing

| Case | Route | Chat-model calls | Cost |
|---|---|---|---|
| stage 1 greeting | fixed greeting plus the protocol's next question | 0 | 0 |
| stage 1 refuse | fixed scope reply | 0 | 0 |
| stage 1 pass | chain | as usual | as usual |
| undecided, `P <= T_refuse` (only when a refusing threshold exists) | fixed scope reply | 0 | one Jev call |
| undecided, `P >= T_pass` | chain | as usual | plus one Jev call |
| undecided, between | chain, `band` flag (the D108 reply check runs on every discovery reply) | as usual | plus one Jev call |
| Jev failure or no client | chain, `gate_failed` (D117, never a refusal) | as usual | the call's cost if any |

Before `gate_dev.json` exists, stage 2 never refuses: every undecided message is `band`. The owner message of a
refused turn is still stored, so the D22 taint stays sticky; traces carry route, stage, P, cost and state hash, never
the text (D141). `build_discovery_app` defaults to stage 1 only (stage 2 fails open). The live product gate is
`milo.gate.build_gate(settings)`: with an OpenRouter key it adds Jev, each call checked and recorded in the spend ledger;
without one it is stage 1 only. The integrator wires it into `create_app` after M6. A `gate_dev.json` whose
`rubric_version` differs from the code's is ignored (stage 2 then never refuses), and `--final` refuses it.

## Thresholds (protocol rule, dev only)

`T_refuse` is the largest out-of-scope dev P strictly below the smallest in-scope dev P (none: 0 and stage 2 refuses
nothing); `T_pass` is the smallest in-scope dev P strictly above the largest out-of-scope dev P (none: 1.0). Only
undecided dev scope rows with a probability count; probes and failed calls do not. `gate_dev.json` records both values
with their `sid`s, `min_in`, `max_out` and the band width. **Pending the dev probe run.**

## Stage 1 on dev (lexicon v4, from `make eval-gate-replay` on the dry result)

| Label | greeting | pass | refuse | undecided |
|---|---|---|---|---|
| in_scope (142) | 2 | 84 | 0 | 56 |
| out_of_scope (68) | 0 | 0 | 14 | 54 |

Zero in-scope dev rows refused by stage 1; 110 dev rows and 41 probes reach stage 2 (two empty probes are not sent).

## Runs

- Dry (done, offline): `uv run python evals/gate/run_gate.py --dry` writes `gate_dry.json` and `jev_replay_dry.jsonl`
  from `evals/gate/fixtures/jev_dry.json` (synthetic). `make eval-gate-replay` and `make eval` replay it.
- Dev probe run (pending): `MILO_LIVE=1 make eval-gate GATE_ARGS='--max-cost 0.05 --reason "stage-2 probability needed to set or confirm a threshold"'`.
  151 Jev calls at about 900 input tokens each, about 0.14 M tokens, about 0.006 USD at 0.042 USD per million
  (output billing to verify). Writes `gate_dev.json` and `jev_replay.jsonl`; commit both, then the product refuses on
  the dev thresholds.
- Test run (pending, once): `MILO_FINAL=1 MILO_LIVE=1 uv run python evals/gate/run_gate.py --final --reason "..."`;
  refuses without a committed `gate_dev.json`, with a rubric mismatch, with `--out`, when `gate_test.json` exists, or
  when the dev cost per call is unknown or 0 (the D116 budget check needs it). Replay of a dev result reads no test rows.
- SR cases A-04, A-05b, A-22: `--gate milo.gate:sr_gate`. The callable does not run the chain, so
  `d108_reply_check_passed` is `None` and A-22 counts only the gate's own refusals.

## Limits

- The Decisions response shape is from the documentation, not yet seen live; the first live call verifies it (the live
  and final runs stop after one call when it fails, and keep that record). The
  parser accepts the documented `answers.<key>.noul` and a few variants and fails open on anything else.
- `scripts/live_check.py` still lists the M5G row as "no runner yet" (integrator-owned).
- An off-topic request naming one strong term passes stage 1 (D158 accepted limit); the D108 reply check covers it.
