# Resume file for the integrator

Purpose: if the session stops (usage limit, crash, compaction loss), a fresh session reads this file first and continues without the owner re-explaining. The integrator updates it at every merge and every agent hand-off, together with `monitor/status.js`.

## Standing rules (owner, 2026-10-22)

- Autonomous run: the integrator decides non-critical items alone and logs them in `docs/integrator-decisions.md`; only risk acceptance, production use, spend beyond caps, or irreversible or outward-facing actions go to the owner.
- Spend: session agents on the Claude subscription do all writing, data, audits, reviews and code. OpenRouter only for measured model comparisons, the product's test-split runs and the final run (HARNESS rule 14, D126). OpenRouter spend so far in this run: 0 USD.
- Read SPEC.md (v0.9.7, sections 20 to 28 override earlier text) and HARNESS.md (v0.10) before anything else.
- Pushes to `origin main` are allowed. Agent branches are never pushed; the integrator merges after a verifier PASS.
- Worktrees live in `.worktrees/<lane>` on branches `agent/<lane>`, `verify/<lane>`, `agent/data/<x>`, `agent/audit/<x>`. Do not use the Agent tool's own worktree isolation (it clones the parent repo, decision 18).
- Build agents: Sonnet; design, audit, security review and Postgres verification: Opus (Fable limit reached 17:06, decision 54; return to Fable only if the owner says so); verifiers: Sonnet, or Opus for auth, store, web, gate, tools.
- Monitor times come from `date` or git only (decision 56). Commit attribution lines name the model the session runs on.
- Owner messages during the run are listed in `monitor/status.js` (`owner_messages`); the autonomous run started 2026-10-22 13:00:01 local.

## How to resume

1. `git status`, `git worktree list`, `git branch --list 'agent/*' 'verify/*'` in the main checkout.
2. Read the "State" section below and `docs/integrator-decisions.md` (last rows).
3. For each branch that exists: if `verify/<x>` has a `docs/verify/<x>.md` with PASS, merge it into main (`git merge --no-ff`), rerun `make check` and the seven kit targets on main, remove the worktrees, append the checkpoint block to `docs/owner/CHECKPOINTS_PENDING.md`. If a build branch has no verifier yet, open `verify/<x>` from it and launch a verifier with the HARNESS section 7 checklist. If an agent was mid-task, its branch holds whatever it committed; relaunch the task with the same brief (the briefs are in this session's transcript; the file ownership is in HARNESS Appendix A).
4. Update `monitor/status.js` and this file.

## State at last update (2026-10-22 17:42 local)

Pauses so far: 14:00 to 16:31 (usage limit), 17:06 to 17:21 (Fable limit). Session on Opus since 17:21.

Merged on main: S0, M5a, M3, M6 shell, M4, M0b, M1 (offline), M0, M2 (offline), live-prep, M5G scope data, SR live runner, M7 (offline). Seven protocols plus m2-ab and m7-judge.

| Branch | Where | Status | Next |
|---|---|---|---|
| agent/m5-store (bdcf7a3) + verify/m5-store (2b13e7b) | `.worktrees/m5-store`, `.worktrees/verify-m5-store` | Offline green (507 tests). Postgres pass 3 BLOCKED: the permission classifier refused the verifier's DB commands; owner asked for permission. The test project still holds fix-cycle-2 objects (reset.py now handles them) | On owner yes: relaunch an Opus verifier for reset, migrate, RLS test, reset; on PASS merge |
| agent/m5b | `.worktrees/m5b` | Opus builder running (discovery core), based on main plus agent/m5-store | Verifier + SR review; merge after M5-store |

Blocked on the owner: (1) OpenRouter balance before the live batch (estimate 1.65 USD; `make live-check`); (2) Postgres permission for M5-store.
Not started: live batch (E01c, M1 index and eval, E02, M2 A/B, M3 tools, M4 arms, M7 generation, then labels, judge prompts, judge; SR live; M5G probes after M5G), M5c, M5G build, M6 wiring, SR pen test, F1 to F4.

Owner checklist (SPEC section 18): items 1 to 10; none blocks the build.
