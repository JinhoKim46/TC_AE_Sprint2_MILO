# MILO — Agent Harness

Status: v0.6, matches `SPEC.md` v0.7. Read `SPEC.md` first. If a rule here and `SPEC.md` disagree, stop and write a request in `docs/requests/` instead of guessing.

## 1. Roles

Every role below is a SPEC section 13 phase entry. The integrator is one role (it may run as one or more agents, but never two at once on the same path).

| Role | Phase | Writes | Never writes |
|---|---|---|---|
| Integrator | 0, 4 and merges | everything listed as (I) in Appendix A; `main`; `verify/*` merges; `docs/owner/CHECKPOINTS_PENDING.md` | owners' module bodies (it merges them, it does not rewrite them) |
| Data (M0) | 1a | `data/**`, `protocols/**`, `scripts/build_data.py`, `tests/test_checker.py`, `tests/test_leakage.py` | `src/**`, `evals/**` (except `evals/agent/attacks_kb/**`, which M0 owns) |
| Tools and pricing (M3) | 1a | `src/milo/tools/**`, `src/milo/pricing/{rate_card,estimator}.py`, `evals/agent/run_tools.py`, its tests and report | `security/**`, `llm/**` |
| LLM and errors (M5a) | 1a | `src/milo/llm/**`, `tests/test_llm.py`, `tests/test_errors.py`, `docs/m5a_report.md` | `errors.py` (I), `app.py` (I) |
| Store and auth (M5-store) | 1a | `src/milo/store/**`, `src/milo/auth/**`, `store/schema.sql`, `tests/test_store.py`, `tests/test_auth.py`, `tests/test_store_identity.py` | repositories' callers in `web/` |
| Web shell (M6, phase 1a part) | 1a | `src/milo/web/**` shell, static files, `tests/test_web_shell.py` | `App` internals |
| Retrieval (M1) | 1b | `src/milo/rag/{chunk,ingest,vectors,retrieve}.py`, `evals/retrieval/run_retrieval.py`, `tests/test_chunk.py`, `tests/test_vectors.py`, `docs/m1_report.md` | `data/**`, `src/milo/security/**` |
| Security (M4) | 2 | `src/milo/security/{policy,identity,taint}.py`, `evals/agent/run_attacks.py`, `tests/test_policy.py`, `tests/test_identity.py`, `docs/m4_report.md` | `tools/specs.py` (request a change) |
| Discovery (M5b) | 2 | `src/milo/{protocol,facts,chain,turns,prompts}/**`, `evals/discovery/**`, `tests/test_protocol.py`, `tests/test_turns.py`, `docs/m5_report.md`, `docs/traces/` | `app.py` (I) |
| Advanced RAG (M2) | 2 | `src/milo/rag/{translate,selfquery,bm25,fusion,rerank}.py`, `evals/retrieval/ab.py`, `tests/test_selfquery.py`, `tests/test_fusion.py`, `docs/m2_report.md` | M1 files |
| Report (M5c) | 3 | `src/milo/report/**`, `tests/test_report.py` | everything else |
| Web wiring (M6) | 3 | `src/milo/web/**` (wiring), `evals/ui/record.py`, `tests/test_web.py`, `docs/m6_checklist.md`, `docs/m6_recording.webm`, `evals/results/m6/**` | `App` internals |
| Labelling (M7) | 3 | `evals/rag/labels.jsonl` only; committed before the judge prompt | everything else |
| Evaluation (M7) | 3 | `evals/rag/**` (except labels), `docs/m7_report.md`, `docs/reflection.md` | `src/**`, `Makefile` |
| Verifier | after each owner's merge | `docs/verify/<milestone>.md` on the `verify/<milestone>` branch | any code; it runs commands, never fixes |

The number of agents is not fixed. A role may be split into several agents with disjoint paths. Labelling and evaluation must be different agents.

## 2. Isolation and Git

- Each owner works in its own worktree outside the AE repo: `git worktree add ~/milo-worktrees/<role>-<milestone> -b agent/<role>/<milestone> main`.
- An owner commits only on its own branch, in the SPEC D30 format, with the trailers the session harness supplies.
- Only the integrator pushes, and only to `origin/main` (the private MILO repo). The owner's approval for pushing MILO is recorded in SPEC D29.
- Verifiers commit only `docs/verify/<milestone>.md` on `verify/<milestone>`. The integrator merges those branches too.
- The AE repo is touched only by the integrator, and only to update the submodule pointer.
- No agent edits the kit (`project-kit/`), the AE repo's other folders, or another role's paths.
- Temporary files go in the agent's scratch folder, never in the repo.

## 3. Hard rules (any violation fails the milestone)

1. **No cloud call.** No agent sets `MILO_LIVE`, instantiates `ChatOpenAI` or an OpenRouter client outside `src/milo/llm/cloud.py`, or reads an API key. A verifier greps `openrouter.ai` and `ChatOpenAI(` under `src/`, `evals/`, `tests/` and fails any hit outside `llm/cloud.py`.
2. **Offline tests.** `addopts = --disable-socket`. Tests never download, start Ollama or load a model. Embedding models are loaded only under `evals/` and in `create_app`, never under `tests/`. `HF_HUB_OFFLINE=1` is set in code before any model loads.
3. **No secrets, no machine paths.** No keys, tokens, `.env` content or absolute home paths in code, data, logs, commits or verifier pastes. Verifiers replace `$HOME` with `~`.
4. **Fictional identities only.** Companies, clients, people, addresses, prices and orders are invented. No real company or person is named, and no real domain is used.
5. **Hidden names.** The owner's business project name, and the names of any other projects the owner keeps out of MILO, are never written into MILO files, commit messages or reports. The list of such names lives only in the untracked `.privacy-terms` file, and the privacy check enforces it.
6. **Test split sealed.** Among the code under `src/`, `evals/` and `tests/`, only `evals/common.py` names `gold.jsonl`. The two leakage tests and the builder are excluded, as SPEC 10.1 says. Only `make final-test` sets `MILO_FINAL=1`.
7. **No self-approval.** An owner never writes into `docs/verify/` and never marks its own milestone done.
8. **Contract files are read-only.** `contract/` is copied from the kit. A test that looks wrong gets a request, not an edit.
9. **No silent skip.** `skip` and `xfail` are not allowed in a done-line. A test that needs a local model is marked `local` and is excluded from the default run. Running it is an explicit make target, and the report says which tests ran in that mode.
10. **Retries in one place.** Only `src/milo/llm/retry.py` retries a model call (SPEC D23a).

## 4. Milestone cycle (no human in the loop)

```text
owner builds in its worktree, runs its quick checks, commits on its branch
  -> verifier (a fresh agent that reads SPEC and the branch, not the owner's notes):
       runs the done-line through the make targets and pytest paths in SPEC 17
       checks every hard rule in section 3 by grep and file checks
       checks owned-path discipline: git diff --name-only main...<branch> must lie inside the owner's rows in Appendix A
       checks every reported number has an evals/results file with the provenance fields of SPEC 10.3
       replays every dev eval and compares rows (section 7)
       writes docs/verify/<milestone>.md on verify/<milestone>
       PASS -> integrator merges
       FAIL -> the owner receives the failing output and fixes it in a fresh fix cycle
               (a new agent, given the failing output and SPEC, not the previous agent's reasoning)
               max 3 fix cycles; after 3 failures the milestone is blocked and independent work continues
  -> on merge, the integrator appends the checkpoint block to docs/owner/CHECKPOINTS_PENDING.md
```

No stop at a milestone. Checkpoint answers stay with the owner (SPEC D31).

## 5. Quality gates on every merge

- `make check` passes. It runs ruff, mypy, pytest, and the data checks once `data/gold.jsonl` exists. The size check excludes `docs/m6_recording.webm`.
- The `make kit-<keyword>` targets for the merged milestone pass (SPEC 13).
- `uv sync` is allowed in every worktree. It is a sanctioned download step (SPEC 11.3).
- The integrator reruns the merged milestones' targets on `main` after the merge. A regression reverts the merge.

## 6. Requests between roles

- File: `docs/requests/<from-role>-to-<to-role>-<slug>.md`, committed on the requester's branch.
- Content: the exact path and function, the change asked for, why, and the test that will show it works.
- The receiving role answers in the same file (accepted or rejected, with a reason) and changes only its own paths.
- Changes to integrator-owned paths are requests to the integrator, who applies them at merge time.

## 7. Verifier checklist (every milestone)

1. Check out the branch in a fresh worktree. Do not read the owner's notes.
2. Run the done-line commands exactly as SPEC 17 writes them. Paste the output with `$HOME` replaced by `~`.
3. Run the hard-rule checks in section 3 and paste the results.
4. Run `git diff --name-only main...<branch>`. Every path must lie inside the owner's rows in Appendix A.
5. For each number in the milestone's report, confirm an `evals/results/<milestone>/*.json` file with `git_commit`, `model`, `seed`, `temperature`, `command`, `timestamp`, `inputs` and `rows`.
6. Replay every dev eval of the milestone with its `make ...-replay` target. The replay must match the stored rows exactly.
7. Dense-only M1 retrieval is also rerun live and compared within 0.005. Model-driven arms (M2 rewrite and self-query, M4, M5, M7) are reported with their live rerun, and a difference is noted in the verdict, not failed.
8. Write the verdict (PASS or FAIL) with evidence to `docs/verify/<milestone>.md` on `verify/<milestone>`.

## 8. Red-team loop (for SPEC and HARNESS)

- A red-team agent reads `SPEC.md` and `HARNESS.md` and tries to break them: contradictions, missing decisions, paths with no owner or two owners, impossible or vacuous done-lines, ways to cheat a verifier, leaks of the test split, cloud calls, and kit mismatches. It verifies claimed fixes by running things, not only by reading.
- Each break gets a severity (blocker, major, minor), a section and a fix.
- The integrator fixes SPEC or HARNESS, bumps the version line, and sends the new version to a **new** red-team agent.
- Stop: a full round finds no blocker or major. Cap: 5 rounds. Blockers still open at the cap: the build does not start, and the owner is told.

## 9. Decisions and escalation

- Decisions already in SPEC are not re-asked.
- A question SPEC does not answer: the integrator picks the option that is local, offline, reversible and within the kit rules, records it as a new `Dnn` decision with a version-log line, and continues.
- A conflict that would break a section 3 rule stops work for the owner.

## 10. Final report

Per milestone: status (`verified, owner review pending`, or `blocked`), the done-line output, the report numbers with CIs and provenance files, every decision made during the build (new `Dnn` entries and version lines), and every open item. Written by the integrator to `docs/owner/CHECKPOINTS_PENDING.md` and the README status line.

## Appendix A. File ownership

One owner per path. (I) marks integrator-owned. A path not listed belongs to nobody and must be requested.

| Path | Owner |
|---|---|
| `pyproject.toml`, `uv.lock`, `.python-version`, `Makefile`, `pytest.ini`, `conftest.py`, `.gitignore`, `.env.example`, `README.md`, `SPEC.md`, `HARNESS.md` | (I) |
| `contract/**`, `scripts/check_project_data.py` | read-only copies from S0 (I) |
| `src/milo/__init__.py`, `app.py`, `types.py`, `errors.py`, `settings.py`, `trace.py`, `spend.py` | (I) |
| `src/milo/security/validate.py`, `src/milo/protocol/schema.py`, `src/milo/pricing/schema.py`, `src/milo/store/schema.sql` | (I) at S0; then the owner named below may change them by request |
| `src/milo/rag/retrieve.py` | (I) S0 seam; M1 replaces the body |
| `src/milo/security/policy.py`, `src/milo/security/taint.py`, `src/milo/loop.py`, `src/milo/llm/retry.py` | (I) S0 seams; M4, M3, M5a replace the bodies |
| `src/milo/llm/**` | M5a |
| `src/milo/rag/chunk.py`, `ingest.py`, `vectors.py` | M1 |
| `src/milo/rag/translate.py`, `selfquery.py`, `bm25.py`, `fusion.py`, `rerank.py` | M2 |
| `src/milo/tools/**`, `src/milo/pricing/rate_card.py`, `src/milo/pricing/estimator.py` | M3 |
| `src/milo/security/identity.py`, `src/milo/security/policy.py` (body), `src/milo/security/taint.py` (body) | M4 |
| `src/milo/protocol/**`, `src/milo/facts/**`, `src/milo/chain.py`, `src/milo/turns.py`, `src/milo/prompts/**` | M5b |
| `src/milo/store/**`, `src/milo/auth/**` | M5-store |
| `src/milo/report/**` | M5c |
| `src/milo/web/**` (shell) | M6 shell (phase 1a) |
| `src/milo/web/**` (wiring) | M6 (phase 3) |
| `protocols/**`, `data/**`, `evals/agent/attacks_kb/**` | M0 |
| `scripts/build_data.py` | M0 |
| `tests/conftest.py` | (I) — not used; the root `conftest.py` is (I) |
| `tests/canary.txt` | (I) at S0 |
| `tests/test_checker.py`, `tests/test_leakage.py` | M0 |
| `tests/test_chunk.py`, `tests/test_vectors.py` | M1 |
| `tests/test_selfquery.py`, `tests/test_fusion.py` | M2 |
| `tests/test_tools.py`, `tests/test_pricing.py` | M3 |
| `tests/test_policy.py`, `tests/test_identity.py` | M4 |
| `tests/test_llm.py`, `tests/test_errors.py` | M5a |
| `tests/test_store.py`, `tests/test_auth.py`, `tests/test_store_identity.py` | M5-store |
| `tests/test_protocol.py`, `tests/test_turns.py` | M5b |
| `tests/test_report.py` | M5c |
| `tests/test_web_shell.py` | M6 shell |
| `tests/test_web.py` | M6 (phase 3) |
| `evals/common.py` | (I) |
| `evals/run_all_replay.py` | (I) |
| `evals/retrieval/run_retrieval.py`, `evals/retrieval/**` (report template) | M1 |
| `evals/retrieval/ab.py` | M2 |
| `evals/agent/run_tools.py` | M3 |
| `evals/agent/run_attacks.py` | M4 |
| `evals/discovery/**` | M5b |
| `evals/ui/record.py`, `evals/results/m6/**` | M6 (phase 3) |
| `evals/rag/labels.jsonl` | Labelling (M7) |
| `evals/rag/**` (other) | M7 |
| `evals/final/**` | (I), F2 only |
| `evals/results/test_access.log` | (I), written only by `make final-test` |
| `evals/results/<milestone>/**` | written only by the eval run that produced it, on that milestone's branch; never hand-edited |
| `docs/m1_report.md` ... `docs/m7_report.md` (one per milestone) | the owner of that milestone |
| `docs/m5a_report.md` | M5a |
| `docs/m5_report.md` | M5b |
| `docs/m6_checklist.md`, `docs/m6_recording.webm` | M6 |
| `docs/reflection.md` | M7 |
| `docs/traces/**` | M5b |
| `docs/verify/**` | verifiers |
| `docs/requests/**` | requester creates; integrator triages |
| `docs/owner/CHECKPOINTS_PENDING.md` | (I) |
| `docs/design/**` | git-ignored; not in the repo build |
| `logs/**`, `data/index/**`, `data/milo.sqlite` | generated; git-ignored |

Overlap rule: a path may appear in two rows only where the row says so (an S0 seam replaced by its owner). Any other overlap is resolved by the integrator at merge time and recorded as a finding.
