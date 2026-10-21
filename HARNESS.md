# MILO — Agent Harness

Status: v0.4, matches `SPEC.md` v0.4. Read `SPEC.md` first. If a rule here and `SPEC.md` disagree, stop and write a request in
`docs/requests/` instead of guessing.

## 1. Roles

| Role | Writes | Never writes |
|---|---|---|
| Scaffold (S0) | section 11 files; the stub files listed in SPEC 17 | any owned module body |
| Data (M0) | `data/**`, `protocols/`, `evals/agent/attacks_kb/`, `tests/test_checker.py`, `tests/test_leakage.py` | `src/**`, `evals/**` except the attacks folder |
| Retrieval (M1, M2) | `src/milo/rag/**` (per file list in appendix A), `evals/retrieval/**`, their tests and reports | `data/gold*`, `tools/`, `security/` |
| Tools and pricing (M3) | `src/milo/tools/**`, `src/milo/pricing/**`, `src/milo/loop.py`, `evals/agent/run_tools.py`, their tests and report | `security/**` |
| Security (M4) | `src/milo/security/**`, `evals/agent/run_attacks.py`, its tests and report | `tools/specs.py` (request a change) |
| LLM and errors (M5a) | `src/milo/llm/**`, its tests and report section | `errors.py` (integrator-owned; send a request) |
| Protocol, facts, chain, turns, approvals (M5b) | `src/milo/{protocol,facts,chain,turns,store,auth}/**`, `evals/discovery/**`, tests, report, traces | `app.py` (integrator), `llm/**`, `security/**` |
| Report (M5c) | `src/milo/report/**`, its test | everything else |
| Web (M6) | `src/milo/web/**`, `evals/ui/record.py`, `tests/test_web.py`, `docs/m6_*` | `App` internals; it uses the public contract only |
| Evaluation (M7) | `evals/rag/**`, `docs/m7_report.md`, `docs/reflection.md` | `src/**`, the `Makefile` |
| Verifier (one per milestone) | `docs/verify/<milestone>.md` on the `verify/<milestone>` branch | any code; it may run commands and may not fix |
| Integrator | `main`, `SPEC.md` version log and new `Dnn` decisions, `Makefile`, `pytest.ini`, `pyproject.toml`, `README.md`, `src/milo/{app,types,errors}.py`, `docs/owner/CHECKPOINTS_PENDING.md`, `docs/requests/` triage | owners' modules (it merges, it does not rewrite them) |

The number of agents is not fixed. A role may be split into several agents with disjoint paths. A data-builder and a data-checker must be different agents.

## 2. Isolation

- Each agent works in its own git worktree outside the AE repo: `git worktree add ~/milo-worktrees/<role>-<milestone> -b agent/<role>/<milestone> main`.
- An agent reads from `main` and writes only in its worktree and branch.
- No agent edits the kit (`project-kit/`), the AE repo, or another milestone's paths.
- Agents do not push. Nothing leaves the machine.
- Temporary files go in the agent's scratch directory, never in the repo.

## 3. Hard rules (any violation fails the milestone)

1. **No cloud call.** No agent sets `MILO_LIVE`, instantiates `ChatOpenAI` or any OpenRouter client in a path that tests or evals run, or reads `OPENROUTER_API_KEY` or `OPENAI_API_KEY`. A verifier greps for `openrouter.ai` and `ChatOpenAI(` outside `src/milo/llm/cloud.py`.
2. **Offline tests.** `addopts = --disable-socket`. No test downloads, starts Ollama or loads a model. Embedding models are loaded only in S0 and the M1 eval, with `HF_HUB_OFFLINE=1`.
3. **No secrets.** No keys, tokens, `.env` content, or machine-specific paths in code, data, logs, commits or verifier pastes. Verifiers replace `$HOME` with `~`.
4. **No real identities.** Brightside-style names, clients, people, addresses, prices and orders are fictional. No real company or person is named, and no real domain is used.
5. **No REDACTED.** The word and any project that uses it are never written into MILO files, commit messages or reports.
6. **Test split sealed.** Only the final steps in `evals/final/` (SPEC 10) read `split=test`. The static check in `tests/test_leakage.py` runs on every merge. The SHA-256 of `data/gold.jsonl` is compared with `MANIFEST.json` at F2.
7. **No self-approval.** An owner never writes into `docs/verify/` and never marks its own milestone done. The verifier writes the verdict on its own branch.
8. **Contract files are read-only.** `contract/` is copied from the kit. If a test looks wrong, write a request.
9. **No silent skip.** `skip` and `xfail` need a request and a verifier note. No fallback to a cloud model.
10. **Commits only in your branch**, in the D30 format with both trailers.

## 4. Milestone cycle (no human in the loop)

```text
owner agent builds in its worktree
  -> owner runs its quick checks and commits to its branch
  -> verifier (fresh agent, reads SPEC and the branch, not the owner's reasoning):
       runs the done-line commands through the make kit-* or pytest targets
       checks every hard rule in section 3 with grep and file checks
       checks owned-path discipline (git diff --name-only main...branch)
       checks every reported number has a provenance file in evals/results/
       writes docs/verify/<milestone>.md on verify/<milestone>
       PASS -> integrator merges
       FAIL -> owner receives the failing output, fixes (max 3 cycles)
               after 3 failures: milestone = blocked; integrator continues with independent work
  -> on merge, the integrator appends the milestone's checkpoint block to docs/owner/CHECKPOINTS_PENDING.md
```

No stop at a milestone. Checkpoint answers stay with the owner (SPEC D31).

## 5. Quality gates on every merge

- `uv run ruff check . && uv run ruff format --check .` pass.
- `uv run mypy src/milo` passes.
- `uv run pytest` passes (unit tests only; `testpaths = tests`).
- The kit targets for every merged milestone pass (`make kit-<keyword>`, SPEC 17). The contract directory is never collected by a bare `pytest`.
- `python scripts/check_project_data.py data` prints `OK` after any change to `data/`.
- Size: 2 MB per file, except `docs/m6_recording.webm` (25 MB). No generated index, database, log or model file is committed. Checked by a `make check` rule.
- The integrator reruns the merged milestones' kit targets before merging into `main`. A regression reverts the merge.

## 6. Requests between agents

- File: `docs/requests/<from-role>-to-<to-role>-<slug>.md`, on the requester's branch.
- Content: the exact file and function, the change asked for, why, and the test that will show it works.
- The receiving agent answers in the same file (accepted or rejected, with reason) and changes only its own paths.
- Changes to integrator-owned files are requests to the integrator, who applies them at merge time.

## 7. Verifier checklist (every milestone)

1. Check out the branch in a fresh worktree. Do not read the owner's notes.
2. Run the done-line commands exactly as SPEC 17 writes them. Paste the output with `$HOME` replaced by `~`.
3. Run the hard-rule checks in section 3, paste the results.
4. Run `git diff --name-only main...<branch>`. Every path must be in the owner's appendix A entries.
5. For each number in the milestone's report, confirm a file in `evals/results/` with `git_commit`, `model`, `seed`, `temperature`, `timestamp` and `command`.
6. Rerun the dev targets for the milestone and compare with the stored results within tolerance 0.005 on nDCG and recall.
7. Write the verdict (PASS or FAIL) with evidence to `docs/verify/<milestone>.md` on `verify/<milestone>`.

## 8. Red-team loop (for SPEC and HARNESS)

- A red-team agent gets `SPEC.md` and `HARNESS.md` and tries to break them: contradictions, missing decisions, impossible or vacuous done-lines, shared paths, ways to cheat a verifier, leaks of the test split, cloud calls without `MILO_LIVE`, and kit mismatches.
- Each break gets a severity (blocker, major, minor) and the section.
- The integrator fixes SPEC or HARNESS, bumps the version, and sends the new version to a **new** red-team agent.
- Stop: a full round finds no blocker or major. Cap: 5 rounds. Blockers open at the cap: the build does not start.

## 9. Decisions and escalation

- Decisions already in SPEC are not re-asked.
- A question SPEC does not answer: the integrator chooses the option that is local, offline, reversible and within the kit rules, records it as a new `Dnn` decision with a version-log line, and continues.
- Only a conflict that would break a section 3 rule stops work for the owner.

## 10. Final report (last step)

Per milestone: status (`verified`, `blocked`, `owner review pending`), the done-line output, the report numbers with CIs and provenance files, the decisions made during the build (new `Dnn` entries and version lines), and every open item. Written to `docs/owner/CHECKPOINTS_PENDING.md` and the README status line by the integrator.

## Appendix A. File ownership

One owner per path. "(I)" marks integrator-owned. A path not listed belongs to nobody and must be requested.

| Path | Owner |
|---|---|
| `pyproject.toml`, `uv.lock`, `Makefile`, `pytest.ini`, `.gitignore`, `.env.example`, `README.md`, `SPEC.md`, `HARNESS.md` | (I) |
| `contract/**`, `scripts/check_project_data.py` | S0 (read-only after S0) |
| `src/milo/__init__.py`, `src/milo/app.py`, `src/milo/types.py`, `src/milo/errors.py`, `src/milo/settings.py`, `src/milo/trace.py`, `src/milo/spend.py` | (I) |
| `src/milo/llm/**` | M5a |
| `src/milo/rag/chunk.py`, `ingest.py`, `vectors.py`, `retrieve.py` | M1 |
| `src/milo/rag/translate.py`, `selfquery.py`, `bm25.py`, `fusion.py`, `rerank.py` | M2 |
| `src/milo/tools/**`, `src/milo/pricing/**`, `src/milo/loop.py` | M3 |
| `src/milo/security/**` | M4 |
| `src/milo/protocol/**`, `src/milo/facts/**`, `src/milo/chain.py`, `src/milo/turns.py`, `src/milo/store/**`, `src/milo/auth/**` | M5b |
| `src/milo/report/**` | M5c |
| `src/milo/web/**` | M6 |
| `protocols/**`, `data/**`, `evals/agent/attacks_kb/**` | M0 |
| `tests/conftest.py`, `tests/test_contract_target.py`, `tests/canary.txt` | S0 |
| `tests/test_checker.py`, `tests/test_leakage.py` | M0 |
| `tests/test_chunk.py`, `tests/test_vectors.py` | M1 |
| `tests/test_selfquery.py`, `tests/test_fusion.py` | M2 |
| `tests/test_tools.py`, `tests/test_pricing.py` | M3 |
| `tests/test_policy.py`, `tests/test_identity.py` | M4 |
| `tests/test_errors.py`, `tests/test_llm.py` | M5a |
| `tests/test_protocol.py`, `tests/test_turns.py` | M5b |
| `tests/test_report.py` | M5c |
| `tests/test_web.py` | M6 |
| `evals/retrieval/**` (M1 `run_retrieval.py`, report template; M2 `ab.py`) | M1 (`run_retrieval.py`, template), M2 (`ab.py`) |
| `evals/agent/run_tools.py` | M3 |
| `evals/agent/run_attacks.py` | M4 |
| `evals/discovery/**`, `docs/traces/**` | M5b |
| `evals/ui/record.py` | M6 |
| `evals/rag/**` | M7 |
| `evals/final/**` | integrator (F2 only) |
| `evals/results/**` | written by the eval run that produced it; never hand-edited |
| `docs/m1_report.md` | M1 |
| `docs/m2_report.md` | M2 |
| `docs/m3_report.md` | M3 |
| `docs/m4_report.md` | M4 |
| `docs/m5_report.md` | M5a and M5b (separate sections, merged by the integrator) |
| `docs/m6_checklist.md`, `docs/m6_recording.webm` | M6 |
| `docs/m7_report.md`, `docs/reflection.md` | M7 |
| `docs/verify/**` | verifiers |
| `docs/requests/**` | requester (create); integrator (triage) |
| `docs/owner/CHECKPOINTS_PENDING.md` | (I) |
| `docs/design/**` | published separately; not in the repo build |
| `logs/**`, `data/index/**`, `data/milo.sqlite` | generated; git-ignored |

Shared-file rule: a path in two rows is allowed only where the table says so. The integrator resolves any other overlap at merge time and records it as a finding.
