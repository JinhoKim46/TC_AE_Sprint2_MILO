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

## M4 — Security: policy, identity, taint, attack evaluation (merged 2026-10-22)

- Builder commits 02d425c (protocol first), e136a66, 34b7b96; integrator fix cycle 2 bd908c9 (App.resume_approval through ApprovalService); verifier FAIL then PASS (docs/verify/m4.md), SR review 13 of 15 offline cases pass, the other two belong to M5b (D134) and M5G.
- Dry attack table (scripted attacker, 43 rows): headline destructive ASR 0; auto-approve arm 0 taint-high and 0 cross-owner executions; forced arm 0 matrix mismatches; benign false refusal 0 of 9.
- Found and fixed on the way: a double-approve race and a missing args-hash check on the product path (both high).
- Open: live arms under evals/protocols/m4-attacks.md (integrator, live batch); the approve-time policy runs at taint low in contract mode (no session taint stored; M5b stores it); SR case defects A-07c and A-02a (pre-live pass, decision 44).
- Owner question: none.

## M0b — Gold set expanded to 120 rows with independent audit (merged 2026-10-22)

- Generation a7ab661 and fix cycle 4f3bd8f (data role); audit d22240b and 842a1ea (audit role, separate branch, pass 1: 108 ok / 12 fix; pass 2: 120 ok). Protocol evals/protocols/m0b-gold-expansion.md committed first.
- Counts: identifier 24 (12/12), paraphrase 72 (36/36), unanswerable 24 (12/12); 44 filtered (22/22, keys section 30 and product 14); every article 2 dev / 2 test; 96 of 96 quotes verbatim; no split leakage; gold SHA-256 6141c7e3...37a72 equals MANIFEST.
- Integrator checks (HARNESS 7.9): count check OK, audit file free of the forbidden token, hash matches; MANIFEST rebuilt once because the audit file joined data/ (gold unchanged).
- Owner question: none.

## M1 — Retrieval (merged 2026-10-22, offline)

- Builder 855fd0f; verifier PASS offline (docs/verify/m1.md). 27 new tests; dry replay matches; live refuses without MILO_LIVE.
- Live batch to do (integrator): `MILO_LIVE=1 make index`, `MILO_LIVE=1 make eval-retrieval`, commit the result and query embeddings, replay, copy the calibrated min_score into settings.py, fill docs/m1_report.md. Embedding cost comes from the OpenRouter ledger (cloud.py drops the embedding usage field).
- Owner question: none.

## M0 — Remaining data scope (merged 2026-10-22)

- Builder 86a2ecd; verifier PASS (docs/verify/m0.md). Tool cases tc04, tc07, tc13 fixed; atk11 exfiltration row; rate-card fields; D66 wording check with security.md reworded; checker and leakage tests (28). Gold hash unchanged.
- Owner question: none.

## M2 — Advanced RAG (merged 2026-10-22, offline)

- Builder 2e1abdc (protocol first), 7692330; verifier PASS offline (docs/verify/m2.md). Six arms; self-query fallback tested; paired bootstrap against dense.
- Live A/B in the batch after E02 pins the dev model and the index is built. The dry adopt verdicts come from a scripted fixture and must not be quoted.
- Owner question: none.

## M5G data — Scope set, lexicon, probes (merged 2026-10-22)

- 420 rows (284 in-scope, 136 out-of-scope), lexicon v4 (strong/weak, D157, D158), 80 probes; four audit passes, all 502 rows ok. Stage 1: 0 in-scope refused; 2 of 68 off-topic test rows pass; 56/54 undecided dev rows for the Jev thresholds.
- Known limit accepted: an off-topic message naming one strong term passes stage 1; D108 and SR A-22 cover it.
- Owner question: none.

## M7 — Evaluation, offline build (merged 2026-10-22)

- Protocol m7-judge.md first (5e3c8a9), then runner, replays, report skeletons. `make eval` replays every stored eval. Labels, generation and judge runs are pending the live batch, in the order the M7 report gives (generation, template, labelling agent, labels commit, judge prompts, judge run).
- Owner question: none.

## M5-store — Store, Supabase Auth, JWT, RLS (merged 2026-10-22)

- Four verifier passes; pass 4 PASS on the TEST Supabase project: migrate, reset, migrate, 58 of 58 identity checks, 13 of 13 own probes (FORCE RLS on 17 owner tables, no privileged role flags, cascade and trigger under FORCE), reset to empty. Findings F1 to F10 fixed on the way (PG 16+ role grants, FORCE RLS, reset as admin, probe defects).
- Owner checklist item 10 (role passwords and app strings) remains for production.
- Owner question: none.

## M5b — Discovery core (merged 2026-10-22)

- Verifier PASS twice (docs/verify/m5b.md); dry discovery eval: premature-estimate 0 over 41 turns, evidence verbatim 1.0; discovery-table matches SPEC 14 on 9 of 9.
- Live discovery run pending (product model, reasoning low effort per D159).
- Owner question: none.

## M5c — Report (merged 2026-10-22)

- Verifier PASS (docs/verify/m5c.md) with A-1 recomputed by hand (monthly 1372.50 / 1525.00 / 1830.00, setup 2700); fix cycle 1 for pii in conflicts and derived sources.
- Owner question: none.

## M6 — Web UI wiring (merged 2026-10-22)

- Verifier PASS and SR review 53 of 57 (4 low, fixed); T1 (refresh token readable in the signed cookie) fixed by encrypting the tokens; production guard refuses consent v0 and no operator; live gate wired; record.py 24 of 24 on Chromium.
- Owner question: the muted text colour (D45 #909090 fails AA) and the consent text (checklist item 2) before production.

## F1 to F4 — Final steps (2026-10-22)

- F1: every branch integrated on main. F2: one sealed test-split run (docs/final_report.md; misses reported: M5G test 1 of 142 in-scope refused and 92.6 percent off-topic refused; M5b fact F1 0.643; no M7 judge validated). F3: make check, seven kit targets, make eval, privacy check (0 findings), dependency audit (chromadb server advisories, not reachable). Pen test: 50 of 50 cases, no critical or high (docs/security_report.md).
- Owner questions, collected:
  1. Production checklist, SPEC section 18 items 1 to 10 (consent text, first operator, role passwords and app strings, Supabase Auth URL and key, MFA, token residual).
  2. Muted text colour: D45 #909090 fails AA contrast; a darker colour is in use. Confirm or choose.
  3. Product findings to accept or schedule: M3 tool choice 9 of 15 on the product model; no abstention on unanswerable RAG questions (D160 keeps min_score 0.30); M5G test misses; M5b extraction F1 below 0.85.
