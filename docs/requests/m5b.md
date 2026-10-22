# Requests from M5b

Each request names the path, the change, why, and the test that shows it works.

## To the integrator

1. **`tests/test_s0_smoke.py::test_discovery_and_create_app_are_stubs_until_their_milestones`** (S0, integrator).
   It asserts that `build_discovery_app` and `build_app(..., discovery=True).ask` raise `NotImplementedError`. M5b
   builds them, so `make check` fails on this one assertion and nothing else. Please drop the two discovery
   assertions and keep the `create_app` one, for example:

   ```python
   def test_create_app_is_a_stub_until_its_milestone():
       with pytest.raises(NotImplementedError):
           create_app()
   ```

   Test: `make check` passes (today: 550 passed, this 1 failed).

2. **`src/milo/app.py`, the discovery branch** (written by M5b for you to merge, SPEC 17). Three edits, contract mode
   untouched (all nine kit tests pass): `App.ask` returns `chain.discovery_turn(...)` instead of discarding it;
   `App.resume_approval` starts with `if self.discovery: return chain.resume_approval(...)`; `build_discovery_app`
   takes keyword-only `repos` and `session_facts` and calls `chain.attach`. Test: `make kit-threads` and every
   `kit-*` target in contract mode; `make discovery-table`.

3. **`App.recent_traces` in discovery mode** (section 9 says it reads the `traces` table). The chain writes every
   trace to both the in-memory tracer and `repos.traces`; `recent_traces` still reads the tracer. If you want the
   table, read `app.discovery_runtime.repos.traces` per turn. No test depends on it today.

4. **HARNESS Appendix A rows.** Add `evals/protocols/m5b-*.md` and `evals/results/discovery/**` to the M5b row (they
   follow the general `evals/protocols/**` and `evals/results/<milestone>/**` rows already).

5. **Makefile (optional).** An `eval-discovery-live` target that refuses without `MILO_LIVE=1`, like `eval-chat`:
   `uv run python evals/discovery/run_discovery.py --model ... --max-cost 1.50 --reason ...`.

## To M5-store (applied at merge or in a follow-up)

6. **`sessions.taint`** (`text`, `low` or `high`, default `low`) and a repository method to raise it. M5b keeps the
   sticky session taint in process and re-derives it from the stored owner messages; an upload's or a tool
   output's taint is lost on restart. `create_app` needs the column so the approve-time policy (m4 request) reads
   it after a restart. Test: a session tainted by an upload, a new runtime over the same store, approve denied.
7. **`approvals.args_json`** (`jsonb`). The table stores only the argument hash, so the bound arguments live in
   process (`chain.SessionApprovals`); after a restart the hash check fails and nothing runs (safe, but the owner
   cannot approve). Test: approve after re-creating the runtime runs the tool once.
8. **`evidence.quote`** (`text`, owner-readable under RLS, never in traces, D141). Quotes travel in `fact_recorded`
   events and are cached in process for `get_session_spec` and the panel. Test: `get_session_spec` after a restart
   returns the quote.
9. **A per-account daily spend counter** (D131 `ACCOUNT_DAILY_SPEND_USD`). `rate_limits` counts integers only; M5b
   counts spend in process (`DiscoveryRuntime.record_spend`). A `spend(user_id, day, usd)` table or a micro-USD
   counter would make it hold across workers.
