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

## State at last update (2026-10-22 15:15 local)

Merged on main: S0 (f42d8f1), M5a (723dbca), M3 (dec57a1), protocols (ac31ea3), threat model and attack cases (52be734, ff14df3), settings accessors D153 (d0d5f10).

| Branch | Where | Status | Next |
|---|---|---|---|
| agent/m5-store (22ff99c) + verify/m5-store (2a89058, merged with main) | `.worktrees/m5-store`, `.worktrees/verify-m5-store` | Verifier running: offline checklist plus Postgres run on the TEST project (migrate, RLS via SET ROLE, db-reset) | On PASS merge verify/m5-store; on FAIL fix cycle to the builder |
| agent/m6-shell (626bc99) + verify/m6-shell | `.worktrees/m6-shell`, `.worktrees/verify-m6-shell` | Verifier and SR reviewer running (web attack cases against the dev app) | On PASS merge |
| agent/m4 (e136a66) | `.worktrees/m4` | Builder on fix cycle 1: approval compare-and-set (A-12b), approve-time policy re-run | Then verifier + SR review (Fable), then merge |
| agent/data/m0b (a7ab661) + agent/audit/m0b (d22240b) | `.worktrees/m0b`, `.worktrees/audit-m0b` | Audit pass 1: ok 108, fix 12. Generator applying fixes | Then auditor pass 2 (changed rows + 20 spot checks); then merge data branch and audit file; then M1 and the live batch may start |
| agent/data/m5g (530133f) + agent/audit/m5g | `.worktrees/m5g`, `.worktrees/audit-m5g` | Auditor running (labels, realism, leakage, lexicon, stage-1 simulation) | Fix cycle if needed, then merge; M5G build waits for M5b |

Not started: M0 remaining scope (after M0b lands; same files), M1 (after M0b audit), M2 (after M1), M5b (after M1, M3, M5a, M5-store), M5c, M5G build, M6 wiring, M7, live batch (E01 rerun, E02, M3 live tools, M4 live arms, Jev probes: one protocol check, one budget check, run by the integrator), SR pen test, F1 to F4.

Owner checklist (SPEC section 18): items 1 to 10; none blocks the build.
