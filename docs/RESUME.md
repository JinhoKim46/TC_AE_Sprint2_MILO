# Resume file for the integrator

Purpose: if the session stops (usage limit, crash, compaction loss), a fresh session reads this file first and continues without the owner re-explaining. The integrator updates it at every merge and every agent hand-off, together with `monitor/status.js`.

## Standing rules (owner, 2026-10-22)

- Autonomous run: the integrator decides non-critical items alone and logs them in `docs/integrator-decisions.md`; only risk acceptance, production use, spend beyond caps, or irreversible or outward-facing actions go to the owner.
- Spend: session agents on the Claude subscription do all writing, data, audits, reviews and code. OpenRouter only for measured model comparisons, the product's test-split runs and the final run (HARNESS rule 14, D126). OpenRouter spend so far in this run: 0 USD.
- Read SPEC.md (v0.9.7, sections 20 to 28 override earlier text) and HARNESS.md (v0.10) before anything else.
- Pushes to `origin main` are allowed. Agent branches are never pushed; the integrator merges after a verifier PASS.
- Worktrees live in `.worktrees/<lane>` on branches `agent/<lane>`, `verify/<lane>`, `agent/data/<x>`, `agent/audit/<x>`. Do not use the Agent tool's own worktree isolation (it clones the parent repo, decision 18).
- Build agents: Sonnet; design, audit, security review: Fable; verifiers: Sonnet, or Fable for auth, store, web, gate, tools.
- Owner messages during the run are listed in `monitor/status.js` (`owner_messages`); the autonomous run started 2026-10-22 13:00:01 local.

## How to resume

1. `git status`, `git worktree list`, `git branch --list 'agent/*' 'verify/*'` in the main checkout.
2. Read the "State" section below and `docs/integrator-decisions.md` (last rows).
3. For each branch that exists: if `verify/<x>` has a `docs/verify/<x>.md` with PASS, merge it into main (`git merge --no-ff`), rerun `make check` and the seven kit targets on main, remove the worktrees, append the checkpoint block to `docs/owner/CHECKPOINTS_PENDING.md`. If a build branch has no verifier yet, open `verify/<x>` from it and launch a verifier with the HARNESS section 7 checklist. If an agent was mid-task, its branch holds whatever it committed; relaunch the task with the same brief (the briefs are in this session's transcript; the file ownership is in HARNESS Appendix A).
4. Update `monitor/status.js` and this file.

## State at last update (2026-10-22 18:25 local)

Pause: usage limit 15:30 to 16:31; the three verifiers (M5-store, M4, M6 shell) were resumed by SendMessage from their saved transcripts. If a verifier does not report, relaunch it fresh with the brief; for M5-store first query the test project and run `make db-reset` if 0001 is applied.

Merged on main: S0, M5a, M3, M6 shell (a75cf0c), M4 (after fix cycle 2), M0b gold (b36e231, manifest 8e222e1), seven protocols, threat model, attack cases, settings accessors D153, D157 gate rule.

| Branch | Where | Status | Next |
|---|---|---|---|
| agent/m5-store (23e058c) + verify/m5-store (2640b47) | `.worktrees/m5-store`, `.worktrees/verify-m5-store` | Verifier pass 2 running: second Postgres run on the TEST project after fix cycle 2 (role grant WITH SET, FORCE RLS) | On PASS merge; on FAIL fix cycle 3 |
| agent/m0 (new) | `.worktrees/m0` | Builder running: remaining data scope (tool cases, attack row, rate card fields, D66 check, checker and leakage tests) | Verifier, merge |
| agent/m1 (new) | `.worktrees/m1` | Builder running: chunking, ingest, vector store, dense retrieval, dry/replay runner | Verifier, merge; live index and eval in the live batch |
| agent/data/m5g (9c3f3b0) + agent/audit/m5g (50ab458) | `.worktrees/m5g`, `.worktrees/audit-m5g` | Fix cycle 2 running (4 rows, lexicon v3 strong/weak per D157); then audit pass 3 | Merge both; M5G build waits for M5b |

Not started: M2 (after M1), M5b (after M1, M3, M5a, M5-store), M5c, M5G build, M6 wiring, M7, live batch (E01 rerun, E02, M3 live tools, M4 live arms, Jev probes: one protocol check, one budget check, run by the integrator), SR pen test, F1 to F4.

Owner checklist (SPEC section 18): items 1 to 10; none blocks the build.
