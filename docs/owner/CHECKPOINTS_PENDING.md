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

## M6 phase 1a — Web shell: pages, templates, static files, web security tests (merged 2026-10-22)

- Builder commits a352568, 626bc99; verifier PASS and SR review 0 critical, 0 high (docs/verify/m6-shell.md; 39 checks, 37 pass, 2 low).
- Done-line: 30 web tests (104 total at the time), make check green, seven kit targets pass.
- Open for phase 3 (M6 wiring): JSON 404 code should be `not_found` (D65); MAX_CONTENT_LENGTH to the 5 MB cap (D46); owner check on PATCH/archive/restore; light-theme danger text contrast 4.38:1 (verifier-manual).
- Open for the integrator: `create_app` must call `settings.production_problems` (D137).
- Owner question: the muted text colour from D45 (#909090) fails AA contrast and was darkened; confirm or supply another colour (docs/m6_checklist.md).
