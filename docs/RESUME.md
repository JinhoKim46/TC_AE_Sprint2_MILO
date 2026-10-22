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

## State at last update (2026-10-22 22:46 local): BUILD FINISHED

All milestones S0 to M7, SR (threat model, cases, pen test) and F1 to F4 are merged on main. No worktree or agent branch is open.
Autonomous run: 13:00 to 22:46 (586 min), paused 284 min (usage limits 14:00-16:31 and 20:33-21:31, Fable limit 17:06-17:21, waiting for the owner 18:18-19:18), active 302 min. OpenRouter spend 2.09 USD.
Open: only owner items (docs/owner/CHECKPOINTS_PENDING.md, final block). The AE parent repo's submodule pointer is not updated, because SPEC section 19 blocks AE commits until the owner decides on the AE privacy check.
