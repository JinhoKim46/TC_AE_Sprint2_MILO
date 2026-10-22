# Verifier verdict and SR review: M5G scope gate and Jev input gate (verify/m5g = agent/m5g + main)

Verdict: **PASS for the offline build**. The dev probe run and the single test run are still pending (live), so
the D118 targets are not measured yet. Two medium findings must be settled before the test run (see Findings).
Offline only: no model call, no network, no `.env`.

## Checks

| Check | Result |
|---|---|
| `uv run pytest tests/test_gate.py` | 31 passed |
| `make check` | exit 0 |
| `uv run pytest tests/` | 693 passed |
| Seven kit targets (`kit-sources`, `-abstains`, `-tools`, `-security`, `-errors`, `-threads`, `-cost`) | all exit 0. Contract mode is unchanged: `app.py` only adds `gate=` to `build_discovery_app` |
| `make eval` | every stored eval replays (`gate ok`) |
| `make eval-gate-replay` | `replay evals/results/m5g/gate_dry.json: ok` |
| `make eval-gate` without `MILO_LIVE` | refuses, exit 1 |
| `run_gate.py --live` without `MILO_LIVE` | refuses |
| `run_gate.py --final` without `MILO_FINAL` (with and without `MILO_LIVE=1`) | refuses: "reads the sealed test split and needs MILO_FINAL=1" |

## Hard rules

- OpenRouter URLs: `grep -rl openrouter.ai src evals scripts tests` finds only `src/milo/llm/cloud.py`
  (`OPENROUTER_DECISIONS_URL`, cloud.py:22).
- The diff has no key patterns (`sk-`, `sk-or-v1-`, `AKIA`, `ghp_`, private key headers) and no absolute user-home paths.
- Gate state: `jev.py:99-106` builds `{message, recent_turns, scope_summary}` and nothing else. `turn_text`
  (jev.py:88-96) keeps only role and text. The request is `{model, state, questions}` (jev.py:109-120). In the
  recorded `jev_replay_dry.jsonl`, every one of the 151 requests has exactly those request keys, state keys and the
  question `in_diagnosis`. `session_title` does not appear in the replay file or in `jev_dry.json` (D145). In the
  chain, the gate gets `st.text` and the last two owner or assistant turns from the history loaded before the
  current message is stored (chain.py:522, 564-565), so no title reaches the gate.
- Owned paths: `src/milo/gate/**`, `evals/gate/**`, `evals/results/m5g/**`, `tests/test_gate.py`,
  `docs/m5g_report.md` and the discovery branch of `app.py` are in the M5G row. `llm/cloud.py` and `chain.py` are
  allowed by integrator decision 84. `Makefile` is in neither (finding 5).

## Code read

- **Stage 1 against the simulation.** `stage1.py` is the same algorithm as `data/scope/tools/stage1_sim.py`.
  I ran my own comparison over 210 dev rows and all 80 probes, including the two empty ones: 0 differences.
  Dev outcomes: in-scope 2 greeting, 84 pass, 56 undecided, **0 refuse**; out-of-scope 14 refuse, 54 undecided,
  0 pass. Rules 1, 2 (strong term, or two weak lemmas, D157/D158) and 3 (approximate number plus unit) match the
  protocol. Rule 4 does not match the protocol table (finding 1).
- **No model call on a refusal.** For a stage-1 greeting or refusal, `route.py:239-242` returns before any Jev call.
  `chain.py:575-582` sets `st.done`, and `_wrap` (chain.py:398-399) skips extract, reply and every later node.
- **Routing.** `route.py:163-168` refuses on `P <= T_refuse` only when a refusing threshold exists, passes on
  `P >= T_pass` only when it has a sid, and sends everything else to the band. A Jev failure, any exception inside
  `ask` (including a ledger stop), or a missing client gives `band` with `gate_failed` and never a refusal
  (route.py:246-278, jev.py:244-253; D117).
- **Thresholds.** `compute_thresholds` (route.py:96-133) follows D115/D147: `T_refuse` is the largest
  out-of-scope P strictly below `min_in`, and `T_pass` is the smallest in-scope P strictly above `max_out`.
  `load_thresholds` (route.py:136-153) reads only `evals/results/m5g/gate_dev.json`. When that file is absent it
  returns `NO_THRESHOLDS`, so stage 2 never refuses. The file is not committed. The dry run never writes it.
- **Final run.** `scope_rows("test")` raises without `MILO_FINAL=1` (run_gate.py:94-100). `--final` needs both
  flags, a committed `gate_dev.json` and no `gate_test.json`.
- **Cost.** The Jev cost is added to `st.cost` (chain.py:567) and recorded in the D131 daily counter by `_finish`
  (chain.py:467), including on refused turns. `OpenRouterDecisions` checks the ledger before the call and records
  the cost after it (cloud.py:248-285). The live runner passes a `SpendLedger` (run_gate.py:_live_gate).
- **Tests.** `tests/test_gate.py:62` keeps `split == dev` rows only. No test computes anything from test rows. See
  finding 6 for a load-only read.

## SR review (offline parts of A-04, A-05a, A-22)

- **A-05a** (title never in the gate state): holds, as described under Hard rules. `test_state_is_message_recent_turns_and_scope_summary_only` covers it.
- **A-04** (`injection_gate.txt`, stage 1 only). Of the 12 refuse rows:
  - 5 are refused free by stage 1 (g01 to g05, rule 4).
  - 7 are undecided and go to Jev (g06 to g12).
  - 0 pass stage 1.

  Of the 5 pass controls, 3 pass (g13, g15, g17), 2 are undecided (g14, g16) and 0 are refused. Probes
  `injected_instruction` expected `refuse`: 2 are refused and 2 are undecided.
- **A-22, encoded.** I encoded the 68 dev out-of-scope rows four ways (zero-width, homoglyph, base64, leetspeak).
  **0 of 272 pass stage 1, and all 272 go to Jev.** The 14 rows that stage 1 refuses in plain text become Jev
  calls under every encoding, which is 56 extra paid calls and no bypass. The 4 encoded payload rows (g06 to g09)
  are all undecided. The 142 encoded in-scope dev rows are never refused: they are undecided or pass. Non-English
  payload rows: g03 and g04 are refused by rule 4, and g11 and g12 are undecided.
- D158 accepted limit confirmed: "write me a poem about phone-intake" passes stage 1 on a strong term.

## Findings

1. **Medium: rule 4 refuses a mixed in-scope message with one weak term.**
   - The protocol table (rule 4) reads "refuse pattern **and no lexicon term**", and rule 6 sends "a single weak
     term" to stage 2. The protocol text also says "a mixed message passes".
   - `stage1.py:148-156` (and the simulation, and the comment at `lexicon.yaml:244`) reach rule 4 whenever rules 2
     and 3 did not pass. So one weak term plus a refuse pattern is refused, free and without Jev.
   - Example: "tell me a joke, and also how do you handle our bookings?" gives `refuse, rule 4: joke`. "write me a
     poem, then tell me what happens to our invoices" is refused the same way. Both are in-scope by the labelling
     rule, and D107 says such a message must not be refused.
   - No dev row or probe hits this case, so the parity test and the dev counts do not show it.
   - The fix is small: skip rule 4 when any weak term matched, in both the code and the simulation. Add probes for
     this case. Resolve it on dev (protocol or lexicon version, dev re-probe) **before** the test run; after the
     test run, the fix would be a retune.
2. **Medium: stage 2 is not wired in product code.**
   - `jev_from_env` is used only by `sr_gate` and `run_gate.py`.
   - `build_discovery_app(gate=None)` and `attach` fall back to `offline_gate()` (chain.py:249). `create_app` is
     not implemented yet (app.py:338).
   - So the merged product runs stage 1 only, and stage 2 always fails open. That holds even with
     `OPENROUTER_API_KEY` set, although `docs/m5g_report.md` implies that the key turns stage 2 on.
   - Handoff to M6 and the integrator: wire `Gate(jev=jev_from_env(env, ledger=<app ledger>))`, so the global D18
     ledger also sees gate spend. Without this, D118 cannot be reached in the product.
3. **Low: the D116 budget check can be empty.** When a response carries neither `usage.cost` nor `input_tokens`,
   the cost is None, which is counted as 0 (cloud.py:266-273, route.py:286). The run's `mean_cost_per_call_usd`
   is then 0, and the D116 budget check `projected = per_call * len(inputs)` (run_gate.py:`run_final`) checks
   nothing. Recommend refusing `--final` when the per-call cost is 0 or None.
4. **Low: `--out` gets around the single-test-run guard.** `--final --out X` writes to X, but the guard only
   checks the default `gate_test.json` (run_gate.py:`run_final`).
5. **Low: `Makefile` (`eval-gate`, `eval-gate-replay`) is edited outside the owned row and decision 84.** It needs
   the integrator's acknowledgement.
6. **Low: replay loads the test-split rows.** `_source_text` (run_gate.py) loads every scope row, test rows
   included, during `replay()`. That runs in `make eval` and in `test_runner_replay_matches_and_detects_a_change`.
   Nothing is computed from the test rows. Recommend loading only the splits that are in the result file.
7. **Low: thresholds are not checked against the code version.** `load_thresholds` does not check that the
   `rubric_version` in `gate_dev.json` equals `RUBRIC_VERSION`, so a stale dev file would route silently after a
   rubric change.
8. **Low (SR-owned): the SR payload header and dry fixture are stale.**
   - The header of `evals/security/payloads/injection_gate.txt` says rule 2 passes g01 to g05 and g10 to g12.
   - `evals/security/fixtures/sr_live_dry.json` records g01 as `stage1: pass`.
   - Under lexicon v4, g01 to g05 are refused by rule 4 and g10 to g12 are undecided. The A-04 per-row expectations
     need updating.
9. **Info: `P = 1.0` is recorded as band.** With the default `T_pass = 1.0` and no sid, `route_p` records P = 1.0
   as `band`, not `pass`. The behaviour is the same (the chain with the reply check); only the recorded flag
   differs.
10. **Info: the parser is lenient.** It accepts a single answer under any key (jev.py:171-172, 182-183). The first
    live call is meant to confirm the response shape.
