# MILO — Agent Harness

Status: v0.9, matches `SPEC.md` v0.9.3. Read `SPEC.md` first, including sections 20 to 22, which override earlier text. If a rule here and `SPEC.md` disagree, stop and write a request in `docs/requests/` instead of guessing.

## 1. Roles

Every role is a SPEC section 13 phase entry. The integrator is one role; it may run as one or more agents, never two on the same path at once.

| Role | Phase | Writes | Never writes |
|---|---|---|---|
| Integrator | S0, merges, F1 to F4 | everything marked (I) in Appendix A; `main`; `verify/*` merges; `docs/owner/CHECKPOINTS_PENDING.md`; `docs/history-map.md` | owners' module bodies (it merges them, it does not rewrite them) |
| Data (M0) | 1a | `data/**` (except the gold expansion), `protocols/**`, `scripts/build_data.py`, `tests/test_checker.py`, `tests/test_leakage.py` | `src/**`, `evals/**` except `evals/agent/attacks_kb/**` |
| Gold expansion (M0b) | 1a | `data/source/gold_questions.yaml`, the gold rows in `data/gold.jsonl`, the generation-record entry, `evals/results/retrieval/embedding_dev_v3.json` | the audit file |
| Gold audit (M0b) | 1a, a different agent from Data | `data/prompts/gold-audit.md` only, on `agent/audit/m0b` | everything else |
| Tools and pricing (M3) | 1a | `src/milo/tools/**`, `src/milo/pricing/{rate_card,estimator}.py`, `evals/agent/run_tools.py`, its tests and report | `security/**`, `llm/**` |
| LLM and errors (M5a) | 1a | `src/milo/llm/**`, `tests/test_llm.py`, `tests/test_errors.py`, `docs/m5a_report.md` | `errors.py`, `app.py` (I) |
| Store and auth (M5-store) | 1a | `src/milo/store/**` (including `memory.py`), `src/milo/auth/**` (Supabase Auth client and JWT verification), `db/migrations/**` after S0, `tests/test_store.py`, `tests/test_auth.py`, `tests/test_store_identity.py` | `app.py` (I) |
| Web shell (M6, phase 1a) | 1a | `src/milo/web/**` shell and static files, shell pages, `tests/test_web_shell.py` | `App` internals |
| Retrieval (M1) | 1b | `src/milo/rag/{chunk,ingest,vectors,retrieve}.py`, `evals/retrieval/run_retrieval.py`, `tests/test_chunk.py`, `tests/test_vectors.py`, `docs/m1_report.md` | `data/**`, `security/**` |
| Security (M4) | 2 | `src/milo/security/{policy,identity,taint}.py` (bodies), `evals/agent/run_attacks.py`, `tests/test_policy.py`, `tests/test_identity.py`, `docs/m4_report.md` | `tools/specs.py` (request a change) |
| Discovery (M5b) | 2 | `src/milo/{protocol,facts,turns,prompts}/**`, the body of `src/milo/chain.py`, `evals/discovery/**`, `tests/test_protocol.py`, `tests/test_turns.py`, `docs/m5_report.md`, `docs/traces/` | `app.py` (I) |
| Advanced RAG (M2) | 2 | `src/milo/rag/{translate,selfquery,bm25,fusion,rerank}.py`, `evals/retrieval/ab.py`, `tests/test_selfquery.py`, `tests/test_fusion.py`, `docs/m2_report.md` | M1 files |
| Report (M5c) | 3 | `src/milo/report/**` (canonical JSON), `tests/test_report.py` | the renderer component (M6) |
| Scope and input gate (M5G) | 3 | `src/milo/gate/**`, `data/scope/**`, `evals/gate/**`, `evals/protocols/m5g-*.md`, `tests/test_gate.py`, `docs/m5g_report.md` | `app.py` (I); `chain.py` seam (M5b) |
| Web wiring (M6, phase 3) | 3 | `src/milo/web/**` (wiring and data-bound pages and the report renderer), `evals/ui/record.py`, `tests/test_web.py`, `docs/m6_checklist.md`, `docs/m6_recording.webm`, `evals/results/m6/**` | `App` internals |
| Labelling (M7) | 3 | `evals/rag/labels.jsonl` only; committed before the judge prompt | everything else |
| Evaluation (M7) | 3 | `evals/rag/**` (except labels), `docs/m7_report.md`, `docs/reflection.md` | `src/**`, `Makefile` |
| Verifier | after each owner's merge | `docs/verify/<milestone>.md` on `verify/<milestone>` | any code; it runs commands, never fixes |

Role slugs for branch names (SPEC D29): `data`, `audit`, `tools`, `llm`, `store`, `web`, `retrieval`, `advanced-rag`, `security`, `discovery`, `report`, `labelling`, `eval`.

## 2. Isolation and Git

- Each owner works in its own worktree outside the AE repo: `git worktree add ~/milo-worktrees/<role>-<milestone> -b agent/<role>/<milestone> main`.
- An owner commits only on its own branch, in the SPEC D30 format, with the trailers the session harness supplies.
- Only the integrator pushes, and only to `origin/main` of the private MILO repo.
- Verifiers commit only `docs/verify/<milestone>.md` on `verify/<milestone>`, based on the owner's branch head. The integrator merges those branches too.
- The AE repo is touched only by the integrator, and only to update the submodule pointer.
- The history was rewritten once (SPEC D86). Commit IDs before 2026-10-22 are mapped in `docs/history-map.md`; quote the new IDs.
- No agent edits the kit, the AE repo's other folders, or another role's paths.
- Temporary files go in the agent's scratch folder, never in the repo.

## 3. Hard rules (any violation fails the milestone)

1. **Models only through OpenRouter.** No agent instantiates a model client outside `src/milo/llm/cloud.py`. The OpenRouter URL appears only in `llm/cloud.py`. A verifier greps `openrouter.ai` under `src/`, `evals/`, `tests/` and fails any hit outside it. No local models anywhere.
2. **Offline tests.** `addopts = --disable-socket`. Tests never call a model, the network, or a database server. They use the in-memory store (SPEC D70) and scripted fakes. Only `create_app`, `evals/ui/record.py` and the verifier's RLS test open Postgres (D71).
3. **Database rules.** The Supabase connection strings come from `SUPABASE_test_session_pooler` and `SUPABASE_prod_session_pooler` (kept in the git-ignored `.env`). `supabase.co` and these variable names appear only in `src/milo/settings.py` and `.env.example`. Tests and evals refuse the prod URL (D76). The app connects as the non-superuser role `milo_app` and never uses the service-role key (D74).
4. **No secrets, no machine paths.** No keys, passwords, `.env` content or absolute home paths in code, data, logs, commits or verifier pastes. Verifiers replace `$HOME` with `~`. The generic privacy check covers this; there is no name check (D85).
5. **Fictional identities only.** Companies, clients, people, addresses, prices and orders are invented. No real company or person is named, and no real domain is used.
6. **Test split sealed.** Among the code under `src/`, `evals/` and `tests/`, only `evals/common.py` names `gold.jsonl`. The two leakage tests and the builder are excluded (SPEC 10.1). Only `make final-test` sets `MILO_FINAL=1`. Protocols are committed before their results (D83).
7. **No self-approval.** An owner never writes into `docs/verify/` and never marks its own milestone done.
8. **Contract files are read-only.** `contract/` is copied from the kit. A test that looks wrong gets a request, not an edit.
9. **No silent skip.** `skip` and `xfail` are not allowed in a done-line.
10. **Retries in one place.** Only `src/milo/llm/retry.py` retries a model call (SPEC D23a). A second model call for JSON re-asking goes through `retry.py` and is not a retry.
11. **Scope gate is free.** A refused input makes no model call and costs 0 (SPEC D103, D109). A verifier test checks this with a fake model that fails if it is called.
12. **Self-registration only.** Accounts are created by sign-up (SPEC section 23). No invite step, and no sign-up path can create an operator.
13. **Consent before data.** `POST /messages` and `POST /upload` return 403 `consent_required` until the owner has an `interview` consent row (D81).

## 4. Milestone cycle (no human in the loop)

```text
owner builds in its worktree, runs its quick checks, commits on its branch
  -> verifier (a fresh agent that reads SPEC and the branch, not the owner's notes):
       runs the done-line through the make targets and pytest paths in SPEC 17 and section 21
       checks every hard rule in section 3 by grep and file checks
       checks owned-path discipline: git diff --name-only main...<branch> must lie inside the owner's rows in Appendix A
       checks every reported number has an evals/results file with the provenance fields of SPEC D56
       checks that the protocol file (evals/protocols) is committed before the result file
       replays every dev eval and compares rows (section 7)
       writes docs/verify/<milestone>.md on verify/<milestone>
       PASS -> integrator merges
       FAIL -> a fresh fix-cycle agent gets the failing output and SPEC, not the owner's reasoning
               max 3 fix cycles; after 3 failures the milestone is blocked and independent work continues
  -> on merge, the integrator appends the checkpoint block to docs/owner/CHECKPOINTS_PENDING.md
```

No stop at a milestone. Checkpoint answers stay with the owner (D31).

## 5. Quality gates on every merge

- `make check` passes. It runs ruff, mypy, pytest, `make migrate` against a throwaway schema in the test project when the migration has changed, and the data checks once `data/gold.jsonl` exists. The size check excludes `docs/m6_recording.webm`.
- The kit targets for the merged milestone pass (SPEC 13). `make discovery-table` runs with sockets blocked and must not open a database.
- `uv sync` is allowed in every worktree (SPEC 11.3).
- The integrator reruns the merged milestones' targets on `main` after the merge. A regression reverts the merge.

## 6. Requests between roles

- File: `docs/requests/<from-role>-to-<to-role>-<slug>.md`, committed on the requester's branch.
- Content: the exact path and function, the change asked for, why, and the test that will show it works.
- The receiving role answers in the same file and changes only its own paths.
- Changes to integrator-owned paths are requests to the integrator, applied at merge time.

## 7. Verifier checklist (every milestone)

1. Check out the branch in a fresh worktree. Do not read the owner's notes.
2. Run the done-line commands exactly as SPEC 17 writes them. Paste the output with `$HOME` replaced by `~`.
3. Run the hard-rule checks in section 3 and paste the results.
4. Run `git diff --name-only main...<branch>`. Every path must lie inside the owner's rows in Appendix A.
5. For each number in the milestone's report, confirm an `evals/results/<milestone>/*.json` file with the D56 fields: `git_commit`, `model`, `seed`, `temperature`, `command`, `timestamp`, `inputs`, `rows`.
6. Replay every dev eval of the milestone with its `make ...-replay` target. The replay must match the stored rows exactly.
7. Dense-only M1 retrieval is also rerun live and compared within 0.005. Model-driven arms are reported with their live rerun, and a difference is noted in the verdict, not failed.
8. For M5-store and M6: provision the test project schema, run `make migrate`, run the RLS test (`tests/test_store_identity.py` against Postgres, outside pytest), and drop the schema. The schema name is `milo_<uuid4 hex>` (D77).
9. For M0b: run the gold count check of SPEC section 21 (D78) and confirm `gold-audit.md` has no `reject` and the hash matches `MANIFEST.json`.
10. Write the verdict (PASS or FAIL) with evidence to `docs/verify/<milestone>.md` on `verify/<milestone>`.

## 8. Review loop (closed)

The red-team review loop is closed. Round 6 was applied in SPEC section 21 (D70 to D84) without a further review round. Verifiers check each milestone instead. Any new spec gap is handled by a `Dnn` decision in SPEC, with a version-log line.

## 9. Decisions and escalation

- Decisions already in SPEC are not re-asked.
- A question SPEC does not answer: the integrator picks the option that is local to the repo, reversible and within the kit rules, records it as a new `Dnn` decision with a version-log line, and continues.
- A conflict that would break a section 3 rule stops work for the owner.
- Anything that spends OpenRouter budget needs a protocol committed first (D83) and must stay within the budget plan in D18.

## 10. Final report

Per milestone: status (`verified, owner review pending`, or `blocked`), the done-line output, the report numbers with CIs and provenance files, every decision made during the build (new `Dnn` entries and version lines), and every open item. Written by the integrator to `docs/owner/CHECKPOINTS_PENDING.md` and the README status line.

## Appendix A. File ownership

One owner per path. (I) marks integrator-owned. A path not listed belongs to nobody and must be requested.

| Path | Owner |
|---|---|
| `pyproject.toml`, `uv.lock`, `.python-version`, `Makefile`, `pytest.ini`, `conftest.py`, `.gitignore`, `.env.example`, `README.md`, `SPEC.md`, `HARNESS.md`, `docs/history-map.md`, `docs/owner/CHECKPOINTS_PENDING.md` | (I) |
| `contract/**`, `scripts/check_project_data.py` | read-only copies from S0 (I) |
| `src/milo/__init__.py`, `app.py`, `types.py`, `errors.py`, `settings.py`, `trace.py`, `spend.py` | (I) |
| `data/scope/**`, `data/scope/lexicon.yaml` | M0 (Fable-designed protocol, SPEC D69) |
| `src/milo/security/scope.py` | M4 |
| `src/milo/security/validate.py`, `src/milo/security/identity.py`, `src/milo/protocol/schema.py`, `src/milo/pricing/schema.py`, `src/milo/store/migrate.py`, `db/migrations/0001_init.sql` | (I) at S0; then the owner named below may change them by request (`db/migrations/**` goes to M5-store after S0) |
| `src/milo/rag/retrieve.py`, `src/milo/security/policy.py`, `src/milo/security/taint.py`, `src/milo/loop.py`, `src/milo/llm/retry.py`, `src/milo/chain.py` | (I) S0 seams, each raising or returning a minimal behaviour; the owner named below replaces the body |
| `src/milo/llm/**` | M5a |
| `src/milo/rag/chunk.py`, `ingest.py`, `vectors.py` | M1 |
| `src/milo/rag/translate.py`, `selfquery.py`, `bm25.py`, `fusion.py`, `rerank.py` | M2 |
| `src/milo/tools/**`, `src/milo/pricing/rate_card.py`, `src/milo/pricing/estimator.py` | M3 |
| `src/milo/security/policy.py`, `src/milo/security/taint.py` (bodies) | M4 |
| `src/milo/protocol/**`, `src/milo/facts/**`, `src/milo/turns.py`, `src/milo/prompts/**`, `src/milo/chain.py` (body) | M5b |
| `src/milo/store/**` (including `memory.py`), `src/milo/auth/**`, `db/migrations/**` after S0 | M5-store |
| `src/milo/report/**` | M5c |
| `src/milo/gate/**`, `data/scope/**`, `evals/gate/**`, `tests/test_gate.py`, `docs/m5g_report.md` | M5G |
| `src/milo/web/**` (shell and static files) | M6 (phase 1a) |
| `src/milo/web/**` (wiring, data-bound pages, report renderer) | M6 (phase 3) |
| `protocols/**`, `data/**` (except the gold expansion), `evals/agent/attacks_kb/**`, `scripts/build_data.py` | M0 |
| `data/source/gold_questions.yaml`, gold rows in `data/gold.jsonl`, `evals/results/retrieval/embedding_dev_v3.json` | M0b (gold expansion) |
| `data/prompts/gold-audit.md` | M0b audit (a different agent) |
| `tests/conftest.py` (not used); root `conftest.py` | (I) |
| `tests/test_s0_smoke.py`, `tests/canary.txt` | (I) at S0 |
| `tests/test_checker.py`, `tests/test_leakage.py` | M0 |
| `tests/test_chunk.py`, `tests/test_vectors.py` | M1 |
| `tests/test_selfquery.py`, `tests/test_fusion.py` | M2 |
| `tests/test_tools.py`, `tests/test_pricing.py` | M3 |
| `tests/test_policy.py`, `tests/test_identity.py` | M4 |
| `tests/test_llm.py`, `tests/test_errors.py` | M5a |
| `tests/test_store.py`, `tests/test_auth.py`, `tests/test_store_identity.py` | M5-store |
| `tests/test_protocol.py`, `tests/test_turns.py` | M5b |
| `tests/test_report.py` | M5c |
| `tests/test_web_shell.py` | M6 (phase 1a) |
| `tests/test_web.py` | M6 (phase 3) |
| `evals/common.py`, `evals/run_all_replay.py` | (I) at S0 |
| `evals/retrieval/compare_embeddings.py`, `evals/retrieval/build_embedding_report.py` | M0b (rewrite to load gold through `evals/common.py`) |
| `evals/retrieval/run_retrieval.py`, report template | M1 |
| `evals/retrieval/ab.py` | M2 |
| `evals/agent/run_tools.py` | M3 |
| `evals/agent/run_attacks.py` | M4 |
| `evals/discovery/**` | M5b |
| `evals/protocols/**` | the eval owner of each milestone, committed before its result file |
| `evals/ui/record.py`, `evals/results/m6/**` | M6 (phase 3) |
| `evals/rag/labels.jsonl` | Labelling (M7) |
| `evals/rag/**` (other) | M7 |
| `evals/final/**` | (I), F2 only |
| `evals/results/test_access.log` | (I), written only by `make final-test` |
| `evals/results/<milestone>/**` | written only by the eval run that produced it, on that milestone's branch; never hand-edited |
| `docs/m1_report.md` ... `docs/m7_report.md` | the owner of that milestone |
| `docs/m5a_report.md` | M5a |
| `docs/m5_report.md` | M5b |
| `docs/m6_checklist.md`, `docs/m6_recording.webm` | M6 |
| `docs/reflection.md` | M7 |
| `docs/traces/**` | M5b |
| `docs/verify/**` | verifiers |
| `docs/requests/**` | requester creates; integrator triages |
| `docs/design/**` | git-ignored; not in the repo build |
| `logs/**`, `data/index/**`, `data/milo.sqlite` | generated; git-ignored |

Overlap rule: a path may appear in two rows only where the row says so (an S0 seam replaced by its owner). Any other overlap is resolved by the integrator at merge time and recorded as a finding.
