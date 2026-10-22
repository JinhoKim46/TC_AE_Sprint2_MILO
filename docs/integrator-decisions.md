# Integrator decisions log

The owner's rule (2026-10-22): the integrator decides non-critical questions alone and records them here; critical decisions (risk acceptance, production use, spend beyond the agreed caps, anything irreversible or outward-facing) go to the owner first. Spec-level decisions keep their `Dnnn` ids in `SPEC.md`; this log records the reasoning and the alternatives, in the order they were made.

Format: date, id, what was decided, why, what was rejected. Owner-only items are marked **owner**.

## 2026-10-22

| # | Decision | Why | Rejected |
|---|---|---|---|
| 1 | Threat-model gaps closed in SPEC section 28 (D127 to D146) by the integrator, not by a further review round | 21 gaps were concrete and each had one defensible answer; a review round would cost a day for little | Delegating each to a separate agent |
| 2 | DB variable names follow `.env` (`SUPABASE_*_session_pooler`), not the spec's `MILO_DATABASE_URL_*` (D127) | The `.env` already exists with those names; renaming it is owner work for no gain | Renaming `.env` |
| 3 | Both gate stages under M5G in `src/milo/gate/**` (D128) | One owner for one feature; the M4 `security/scope.py` split came from an earlier version | Splitting stage 1 to M4 |
| 4 | RLS reads `current_setting('milo.user_id')` only (D129) | `auth.uid()` is empty over a psycopg connection; two predicates would silently disagree | Keeping both |
| 5 | Per-account limits: 0.50 USD/day, 1 running turn, 10 messages/min, 5 uploads/h (D131) | Without them one account can drain the global reserve; values are generous for a real owner and tight for abuse | No per-account limit |
| 6 | Uploads parsed in memory, never indexed, capped at 20k chars kept and 8k sent to extract (D132, D133) | Closes traversal, cross-owner retrieval and unbounded cost in one rule | Storing originals; indexing uploads per session |
| 7 | Evidence must be a verbatim substring or the fact is discarded (D134) | Fabricated quotes would reach the report; discard is simpler and safer than a new status | Storing with status `estimated` |
| 8 | Replies rendered as plain text, no markdown (D139) | Removes the sanitizer question and one XSS path; the product does not need formatting | Markdown with a sanitizer |
| 9 | Session title not sent to Jev (D145) | Owner-editable text would reach the judge as context; `recent_turns` already carries what follow-ups need | Sending the title |
| 10 | Rate limits stored in Postgres (D142) | In-process counters reset on restart and do not hold across workers | In-process limiter |
| 11 | Operator MFA and access-token revocation left as **owner** checklist items 8 and 9 (D136, D144) | Both are risk acceptances, which an agent must not make (D125) | Deciding them here |
| 12 | Gate threshold equality resolved towards no false refusal (D147) | The product promise is zero in-scope refusals; the equality case must not break it | Refusing at equality |
| 13 | E02 owned by M5a and run after `llm/cloud.py` and the M0b audit (D148) | E02 needs the OpenRouter wrapper, which M5a writes; before that, tests use fakes so no dev model is needed | Running E02 first with a throwaway client |
| 14 | Demo conversations split A-1/B-1/E-1 dev, A-2/B-2/E-2 test (D149) | One of each company per split; nothing in the spec said otherwise | Random split |
| 15 | S0 started before the protocols were back | S0's files and the protocols' files do not overlap, so waiting bought nothing | Serialising |
| 16 | Gold and scope generators run in isolated worktrees, one branch each, audits follow on separate agents | Matches the protocols' independence rule (HARNESS 7) and keeps the main checkout stable for the attack writer | Generating in the main checkout |
| 17 | S0 accepted as built; its 11 literal readings stand (ruff excludes the two pre-S0 embedding scripts until M0b rewrites them; no torch; `sessions.id` uuid per D98; DB roles created without passwords, owner sets them; cross-owner approval returns `not_found` per D65) | Each follows the latest spec text; `make check` and all 9 kit tests pass in the main checkout | Re-opening any of them |
| 18 | Agent worktrees are created by the integrator inside the MILO repo (`.worktrees/<lane>`, branch `agent/<lane>`), not with the tool's own isolation | The tool's isolation makes a worktree of the parent AE repo, where MILO is an uninitialised submodule; the S0 agent had to copy the project by hand | Tool isolation |
| 19 | `evals/security/cases.jsonl` may name the Supabase variable names (HARNESS rule 3 names only `settings.py` and `.env.example`) because the case tests the prod guard | A guard test has to name what it checks; the verifier grep gets this one exception, written into the SR README | Removing the names from the case |
