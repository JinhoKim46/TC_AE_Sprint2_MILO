## M5a — LLM client, retries, typed errors, E02 runner (merged 2026-10-22)

- Builder commit 639f145, verifier verdict PASS (docs/verify/m5a.md).
- Done-line: 45 new tests, 74 total, kit-errors and kit-cost pass, make check green, eval-chat-replay runs.
- Open for the integrator: pass a spend ledger when wiring E02 live (verifier finding 3); the candidate slugs and per-model temperature/reasoning support are verified at the live run.
- Owner question: none.

## M3 — Tools, MiloBackend, pricing estimator, tool loop (merged 2026-10-22)

- Builder commits fb656b6, cc966c6; verifier verdict PASS (docs/verify/m3.md).
- Done-line: 136 new tests (165 total), kit-tools and the other six pass, make check green, dry tool eval replays at pass rate 1.0.
- Open: live tool run needs `evals/protocols/m3-tools.md` (integrator, with the live batch); `resume_approval` does not re-run the policy (M4); tool_cases tc04, tc07, tc13 stale (M0); whether the 0.90/1.00/1.20 range applies to setup fees (M5c).
- Owner question: none.
