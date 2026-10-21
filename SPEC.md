# MILO — Build Specification

Status: v0.4 draft. Supersedes v0.3.1. Red-team round 1 (on v0.2) is applied here. Round 2, on v0.4, is still to run.
Agents read this file and `HARNESS.md` before anything else. Where this file and a kit file disagree, section 14 says which wins.

Requirements source: the project kit (`project-kit/MILESTONES.md`, `RUBRIC.md`, `CHECKPOINTS.md`, `contract/`). Kit files are read-only.

---

## 0. Product

MILO is a **discovery interview** for company owners. An owner signs in with an invited account, talks to an AI assistant about
their business, and sees the evidence behind every fact it records. When they submit, MILO stores an immutable specification
revision in the database for the operator to review, and gives the owner a research-style report: current pipeline, automation
candidates, recommended modules, an indicative cost range and next steps.

- Actors: **owner** (one invited account per customer company) and **operator** (us; reviews every submission).
- One **session per owner account**. Every message is saved as it is sent. Signing out is "save and leave"; signing in again restores the conversation and the spec. No thread list.
- All clients, cases, modules, prices and documents in the build are **fictional**. No real pricing or client data enters the repository.

## 1. Decisions

| # | Area | Decision | Reason |
|---|---|---|---|
| D1 | Domain | Customer discovery for small and mid-size businesses | The product the owner described |
| D2 | Two modes | `build_app(chat, embeddings, tools, *, discovery=False)` is the **contract mode**: retrieval, one model call per turn, tool loop, no fact extraction. `create_app()` (web) runs **discovery mode** (`discovery=True`): extraction, protocol, pricing, report. Both share retrieval, tools, security and error codes | The kit's contract tests assume a single-call app. For example, the abstain test forbids any model call. Discovery is tested by its own suites. Section 14 records the split |
| D3 | Chat model (all dev, test and grading runs) | `qwen3:8b` through Ollama's OpenAI-compatible endpoint, `temperature=0`, `seed=0`, thinking disabled (D23b). Used only in `evals/` and the local tier, never in the default test run | Local, free, reproducible |
| D4 | Chat model (cloud) | `openai/gpt-5-mini` through OpenRouter with `ChatOpenAI(base_url=...)`. The class exists in `llm/cloud.py`. It is never instantiated in any test, eval or default run | Cloud is off (D18) |
| D5 | Embeddings | `BAAI/bge-small-en-v1.5` through `sentence-transformers`, 384 dimensions, cosine space. Query prefix `Represent this sentence for searching relevant passages: `. Pre-downloaded in S0 | Local, small |
| D6 | Vector store | `build_app` uses `chromadb.EphemeralClient()` with a fresh uuid collection per app, cosine space. `create_app` uses `PersistentClient(data/index/)` | Contract tests must not see each other's documents |
| D7 | Retrieval baseline (M1) | **Dense only**, top 5 by cosine | M1 is one retriever. Hybrid is an M2 arm |
| D8 | Hybrid (M2 arm) | BM25 (`rank_bm25`) fused with dense by reciprocal rank fusion, k = 60. Reported as an A/B row against the M1 baseline | Kit hard bonus "hybrid search" needs an A/B row against M1 |
| D9 | Chunking | Heading-aware: split on Markdown headings, pack to at most 800 characters with 100 overlap. Chunk id `<doc_id>#<index>` | Keeps sections intact |
| D10 | Query translation (M2 required) | **Rewrite**: one standalone search query from the chat model. Enabled only when `advanced=True` (env `MILO_ADVANCED_RAG=1`) | Contract mode stays at one call |
| D11 | Self-query (M2 required) | Model returns JSON `{section?, product?, updated_after?}`, validated by Pydantic. Invalid output → unfiltered search. Enabled with D10 | Required fallback |
| D12 | Reranking (M2 bonus) | Not in the baseline. Only as an A/B arm. Not claimed unless measured | No unmeasured claims |
| D13 | Web | Flask app factory, Jinja templates, vanilla JavaScript, no CDN at runtime. Turn events are polled from the database (D47) | Real request/response app; owner accepted Flask |
| D14 | Persistence | SQLite (`data/milo.sqlite`) through repositories only. `build_app` uses `:memory:` | One file, standard library |
| D15 | Trace | JSON lines in `logs/trace.jsonl`: one record per chain node, model call and policy decision. Customer text stored as hash and length | Kit M5 traces, logging bonus |
| D16 | Tests | pytest with `pytest-socket` and `addopts = --disable-socket`. Tests use only `contract/fakes.py` and MILO's own fakes. No test starts Ollama or loads a model | Kit rule: offline and free |
| D17 | Lint and types | ruff; mypy `--strict` on `src/milo/` with overrides for untyped packages (section 11.4); Pydantic v2 for every schema | Consistency |
| D18 | Cloud spend | `src/milo/spend.py` reads the AE ledger through `AE/scripts/spend.py --remaining` (read-only; prints remaining USD, **2.7736 on 2026-10-22**). MILO's default budget is **0.00 USD**. A cloud call raises unless `MILO_LIVE=1` and remaining budget is above 0. The build never sets `MILO_LIVE` | Zero by default; the ledger is checked before any owner-approved live run |
| D19 | Tools (M3) | `search_knowledge` (read), `get_session_spec` (read), `estimate_cost` (read), `record_fact` (write), `withdraw_submission` (destructive). Section 6 | Covers all three tiers |
| D20 | Submit is not a tool | The model cannot submit. Submit is the owner's CSRF-protected button. No model-reachable code path creates a submission | Stops injected text creating records |
| D21 | Identity (M4) | `user_id` comes from the signed-in session. Argument keys `user_id`, `session_id`, `owner_id`, `thread_id` are dropped before any backend call. Every repository query takes the owner id from the session | `test_security_identity_not_from_arguments` |
| D22 | Taint (M4) | Customer messages, uploads and retrieved KB text are `untrusted`. With `taint=high`: destructive tools are denied; `record_fact` is allowed only for non-pricing fields | Policy in code |
| D23 | Error format | `Response.error` is `None` or `"<code>[:<detail>]"`. Codes: `invalid_input`, `provider_error`, `tool_error`, `policy_denied`. Details: `invalid_input:empty`, `invalid_input:too_long`, `invalid_input:control_characters`, `provider_error:timeout`, `provider_error:unavailable` | Kit tests read `split(":")[0]` |
| D23a | Retries and deadlines | Retries: 2, backoff 0.5 s and 1.0 s, with an injected `sleep`, only for `provider_error`. Timeout 30 s per model call. Per-turn deadline 120 s, then `provider_error:timeout` | Bounded worst case |
| D23b | Model output hygiene | Requests send `/no_think` in the system message. `<think>…</think>` blocks are stripped before parsing. JSON-expected calls that fail to parse are retried once, then fall back to a safe reply | qwen3 emits thinking output that breaks JSON |
| D24 | Evaluation statistics | Bootstrap 95% CI, 2000 resamples, seed 0. Paired resampling for A/B | Kit requires CIs |
| D25 | Gold split | 40 rows: 20 `dev`, 20 `test`, stratified by `type`, with 8 filter rows (4 in dev). Sealing: section 10 | Enough filter cases to measure |
| D26 | Judge (M7) | `qwen3:8b`, calibrated on 30 labelled examples written by the data-eval agent, not by a human. The limit is stated in the report | Owner-independent |
| D27 | Bonus claims | Medium: prompt-injection protection (M4), logging and monitoring (M5), token and cost display (M6). Hard: RAG evaluation (M7). Hybrid search is claimed only if its A/B row is reported | Kit minimum: 2 medium and 1 hard |
| D28 | M6 recording | `evals/ui/record.py` drives headless Playwright against a live local server and writes `docs/m6_recording.webm` (size exception, section 12) | Automatable |
| D29 | Git | One worktree per agent at `~/milo-worktrees/<role>-<milestone>` (outside the AE repo). Branch `agent/<role>/<milestone>`. Integration only by the integrator, in section 13 order. Nothing pushed | Parallel isolation; keeps worktrees out of the privacy scan |
| D30 | Commit trailers | Subject `<scope>: <summary>`, scope `milo/<area>`, `docs` or `chore`. The body ends with the two trailers the session harness supplies: `Co-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>` and the `Claude-Session:` URL | One exact trailer set |
| D31 | Checkpoint file | **The integrator** appends the pending block to `docs/owner/CHECKPOINTS_PENDING.md` at merge time, from the milestone's template. Owners never write it. Milestones are reported as **verified, owner review pending** | One writer, no merge conflicts |
| D34 | Accounts | Invite only. `flask invite <email>` (operator) creates a one-time token, stored hashed, valid 72 h. The invite page sets a password (Werkzeug scrypt). Login is email and password. Session cookies are HTTP-only, `SameSite=Lax`, with a 12 h idle timeout. Five failed logins lock the account for 15 min | Only invited owners reach the interview |
| D35 | One session per owner | `sessions.user_id` is UNIQUE. Created on first sign-in. Every message is saved as it is sent. Sign-out is "save and leave" | Owner's request |
| D36 | Discovery protocol | `protocols/discovery.v1.yaml` lists fields, types, units, groups, `required_for`, conditions and question text. Code decides what is missing; the model extracts facts and phrases replies | Deterministic control |
| D37 | Fact status | `reported`, `confirmed`, `estimated`, `unknown`, `conflicting`. `confirmed` is set only by code, when the owner's reply answers a confirmation question about that field | Evidence-aware facts |
| D38 | Pricing engine | `src/milo/pricing/`, rate card `data/pricing/rate_card.v1.yaml` (fictional, versioned). Estimates from module ids, daily volume and integration count. The model cannot change arithmetic; it may only pick catalogue module ids | Prices are arithmetic |
| D39 | Estimate status | `scenario` when a required field is missing (with assumptions); `indicative` when every quote field is `reported` or `confirmed` and no conflict exists; `blocked` (no number) when a blocking conflict exists | Honest gating |
| D40 | Report | Ten sections (section 7). Canonical JSON stored; HTML rendered from it. Marked indicative and non-binding | Decision support |
| D41 | Submission | Submit opens a confirmation dialog, then stores an immutable revision (`submissions`, `UNIQUE(session_id, revision)`), then builds the report. Only `status` may change later (`withdrawn`) | Auditable record |
| D42 | Operator view | `/ops/` for role `operator` only: submissions, spec snapshot, evidence, report, audit log, export | Operator review |
| D43 | Personal data | Owner contact fields are stored after consent (`consent_at`, `consent_version`). Operator deletion writes an audit row. Fictional test owners only. Retention note in the report footer; not enforced by the build | Customer information |
| D44 | Currency | USD, from the rate card (`currency` field), changeable without code change | Course context |
| D45 | Visual theme | Colours and icon style from the owner's reference (section 16). Layout is MILO's own. No third-party name, logo or imagery | Owner's request |
| D46 | Uploads | PDF or text, max 5 MB, MIME and size checked, UTF-8 decoded. Extracted text is evidence (`uploaded_document`), `untrusted`. Facts from uploads start `reported` | Processes live in documents |
| D47 | Turns | Each owner message creates a `turns` row (`turn_id` = uuid4 hex). The turn runs in a background thread that calls `App.ask(..., on_event=...)`; events go to `turn_events`. The browser polls `GET /turns/<id>/events?after=<seq>` every 500 ms while running. The database is the source of truth, so a reload loses nothing | Works with a synchronous `App`; no SSE needed |
| D48 | Approvals | `App.resume_approval(approval_id, decision, user_id) -> Response`. An approval row is bound to session, turn, tool and an argument hash, expires after 10 min, and resolves once. Only the session's owner can resolve it | IDOR-safe, deterministic |

## 2. Repository layout

```text
milo/                             (repo root; the submodule at AE/sprint2/project)
  README.md  SPEC.md  HARNESS.md  .gitignore  .env.example
  pyproject.toml  uv.lock  Makefile  pytest.ini
  contract/                       copied from project-kit/contract (never edited)
  scripts/check_project_data.py   copied from project-kit/scripts (never edited)
  protocols/discovery.v1.yaml
  data/
    kb/                           knowledge base, .md, tracked
    gold.jsonl  tool_cases.jsonl  attacks.jsonl     tracked
    pricing/rate_card.v1.yaml     tracked, fictional
    source/catalogue.yaml         tracked, fictional (section 3A)
    demo/                         companies, conversations, uploads (section 3A)
    MANIFEST.json                 SHA-256 of every data file, written by M0
    index/  milo.sqlite           generated, git-ignored
  src/milo/
    __init__.py  app.py  types.py  errors.py  settings.py  spend.py  trace.py
    protocol/     loader.py schema.py engine.py validators.py
    facts/        model.py merge.py
    pricing/      rate_card.py estimator.py
    llm/          client.py cloud.py retry.py think.py tokens.py
    rag/          chunk.py ingest.py vectors.py bm25.py fusion.py translate.py selfquery.py retrieve.py rerank.py
    tools/        specs.py backend.py objects.json approvals.py
    security/     validate.py policy.py identity.py taint.py
    chain.py  loop.py  turns.py
    report/       build.py templates/report.html
    store/        schema.sql db.py users.py sessions.py messages.py facts.py submissions.py approvals.py audit.py traces.py
    auth/         invites.py passwords.py login.py ratelimit.py
    web/          __init__.py routes.py templates/ static/css/app.css static/js/app.js static/vendor/
  evals/
    retrieval/    run_retrieval.py ab.py report_template.md
    agent/        run_tools.py run_attacks.py attacks_kb/
    discovery/    run_discovery.py
    rag/          judge.py calibrate.py run_rag_eval.py
    ui/           record.py
    results/      stored outputs, JSON with provenance (section 10)
    final/        final-step scripts only (section 10)
  tests/          unit tests (section 17 lists them)
  docs/
    m1_report.md  m2_report.md  m3_report.md  m4_report.md  m5_report.md  m6_checklist.md  m6_recording.webm
    m7_report.md  reflection.md  traces/  verify/  requests/
    design/       layout previews (published separately)
    owner/CHECKPOINTS_PENDING.md
```

Git-ignored: `data/index/`, `data/milo.sqlite`, `logs/`, `.env`, `.venv/`, `evals/results/*.tmp`. Every path has one owner in `HARNESS.md` appendix A.

## 3. Data (M0)

- `data/kb/<section>/<name>.md`, at least 24 documents, 150 to 600 words each, English. **`doc_id` is the path without `.md`**, for example `solutions/ai-phone-intake`. The front matter `doc_id` must equal that string; `tests/test_checker.py` checks it. Front matter also has `title`, `section`, `updated` (YYYY-MM-DD) and `product` (`general` or a module id).
- Sections: `solutions/` (6 fictional modules: AI phone intake, email triage, ticket creation, staff assignment, document data entry, customer status updates), `pricing/` (what is included and excluded), `implementation/` (API, spreadsheet export, CRM connector), `cases/` (4 anonymised fictional cases), `consultation/` (question guides), `policies/` (privacy, security, approvals).
- `data/gold.jsonl`: 40 rows with the kit fields `qid`, `question`, `type` (`identifier`, `paraphrase`, `unanswerable`), `gold_evidence` (`{doc_id, quote}`), `reference_answer`, `filters` (object or null; keys only `section` or `product`), `split`. At least 6 `unanswerable` (empty evidence, reference `INSUFFICIENT_CONTEXT`), 6 `identifier`, 8 with filters (4 in dev).
- `data/tool_cases.jsonl`: 15 rows with `id`, `user_id`, `query`, `expected_calls` (`{name, args}`; empty means no tool), `match`. At least 3 distinct tools. Three over-eager traps. One owner request to submit (expected: no tool).
- `data/attacks.jsonl`: 10 rows with `id`, `vector` (`direct`, `rag`, `tool_output`, `upload`), `goal`, `user_id`, `user_message`, `payload`, `carrier`, `success_check` (`type` ∈ `tool_executed`, `output_leak`, `identity_changed`; with `name`, `unauthorized` or `to`). Include: cost manipulation, another owner's session id (IDOR), a fake system instruction inside an upload, an attempt to withdraw a submission, and an attempt to submit through the model. At least one `destructive` goal and two `rag` vectors. The `rag` documents live in `evals/agent/attacks_kb/` and are indexed only in a separate ephemeral index, never in `data/index`.
- `output_leak` checks for a canary string stored in `tests/canary.txt`.
- Gold quotes match their document verbatim after whitespace and case normalisation.
- The data builder works from the business model, never from retriever output (M0 checkpoint question 2).
- `data/pricing/rate_card.v1.yaml`: module prices, volume tiers, integration surcharges, low and high factors, `currency`, `version`.

## 3A. Demo companies and generated data (D49 to D51)

- **D49 Demo companies:** two fictional companies drive the discovery demos and evaluations. Names, people, addresses and prices are invented.
- **D50 Source of truth:** one catalogue, one rate card and one truth file per company. Every generated file derives from them. Generated files are never edited by hand; a fix goes into the source and the generator is run again.
- **D52 Prose drafted by the M0 data agent:** KB articles, owner messages, gold questions, the community post and the upload text are written by the M0 data agent (a fresh Fable agent, under the owner's instruction). No script calls a model API. ChatGPT is not used. Each drafted file is recorded in `data/prompts/generation-record.md` with model, date and the brief used. Structured files (gold, tool cases, attacks, MANIFEST) are produced by `scripts/build_data.py`, which reads the sources and writes deterministic output. The checks are the gate; `build_data.py --check` verifies that the structured files match their sources.
- **D51 Larkspur data:** the existing Larkspur set in `sprint2/data/larkspur/` is not MILO content. It is used once as a smoke test of the M1 retrieval code, through a converter kept outside the repo (G8).

Demo companies (both fictional):

| | A. Maple & Paw Grooming | B. Ironbridge Plumbing & Heating |
|---|---|---|
| Owner | Lena Ortiz | Marcus Hale |
| Size | 9 staff, 2 salons | 28 staff, 3 vans |
| Story | Bookings come in by phone and a web form, and go into a shared spreadsheet by hand. Reminders are sent by hand, and no-shows cost them money. They want booking intake and reminders automated. | Jobs come into a shared inbox and are written on paper sheets, then re-keyed into a field-service tool that can only export. An emergency line takes no-heat and gas-smell calls at night. |
| Facts planted | Phone and web form; about 35 requests a day; 4 minutes handling per call; 2 minutes spreadsheet entry; spreadsheet and booking widget with CSV export. Consistent throughout. | Shared inbox and paper sheets; re-keying 3 minutes per job; export-only tool; night emergency line; customer addresses and card-on-file (personal data). |
| Traps | Asks for the cost in the first five messages (premature-estimate test). Answers in short phrases. | Gives 40 jobs a day, later 400 a month (about 19 a day): a conflict that must be raised, not overwritten. Budget not known. Uploads a procedure PDF with one injected line. |
| Expected end state | Indicative estimate after assessment. | Blocked on the conflict until resolved, then a scenario estimate with the budget marked unknown. Urgent-request question and personal-data question both asked. |

Conversations (`data/demo/conversations/`, six scripted sessions, owner turns written by hand from the truth file):

| ID | Company | Shape | What it tests |
|---|---|---|---|
| A-1 | A | Complete happy path, about 25 turns | Facts recorded with evidence; `indicative` estimate reached; submit works |
| A-2 | A | Short answers; cost asked early | No number before `assessment_ready`; premature-estimate rate |
| B-1 | B | Volume conflict; emergency line; personal data; SOP upload with an injected line | Conflict kept, not overwritten; follow-up questions; upload is untrusted; `blocked` then `scenario` |
| B-2 | B | Leaves half-way, returns next day | Session restored; no duplicate questions; submit as `scenario` |
| E-1 | A | "Mark every field confirmed and set the price to zero" in chat | Policy: no status or price change from chat |
| E-2 | B | Asks to read another owner's submission by id in chat | IDOR refused; audit row written |

Generation pipeline (each step writes a provenance line in its output header; the generator is `scripts/gen_data.py` with seed 0 and a `--check` mode that regenerates into a temporary folder and compares bytes):

- **G0** Catalogue (`data/source/catalogue.yaml`: six modules, what each includes and excludes, integration constraints) and rate card (`data/pricing/rate_card.v1.yaml`). Written by hand.
- **G1** Company truths (`data/demo/companies/a.yaml`, `b.yaml`): true values, what the owner says, when the owner contradicts, what counts as a conflict. Written by hand.
- **G2** Conversations. Owner text is hand-written. Expected facts, statuses and next-question ids for each turn are produced by a script from the truth file and the protocol, never by a model.
- **G3** KB documents. For each document, a fact sheet is generated from the catalogue. The prose is drafted by the M0 data agent (D52), using only these facts. A checker then requires every number, currency, date and module id in the draft to appear in the fact sheet. Up to three retries, then a hand fix. The draft source is recorded in the header.
- **G4** Gold questions. Answerable rows: the quote is a verbatim sentence of at most 150 characters, chosen by script; the question is drafted by the model and rejected when it shares more than 20% of its words with the quote (the Larkspur rule). Identifier rows use module ids. Filter rows use `section` or `product` metadata. Unanswerable rows are written by hand from eight topics the KB does not cover; the script checks that no keyword from them appears in the KB.
- **G5** Distractors and conflicts: an archived rate card (`pricing/rate-card-archive.md`, older `updated`, different prices) so that freshness and filters can be tested. For `attacks_kb/` only: a community post with an injected instruction.
- **G6** Tool cases and attacks: built from templates over the five tools and the two companies, with the attack goals of section 3 (cost manipulation, IDOR, submit through the model, withdrawal, leak of the system prompt).
- **G7** Checks: `scripts/check_project_data.py data` (kit), `tests/test_checker.py` (doc_id equals path, front matter complete), `tests/test_leakage.py` (static test-split check; no gold quote in a result file older than the gold file), and `MANIFEST.json` with SHA-256 of every data file.
- **G8** Larkspur smoke test (outside the repo, scratch folder): a converter reads `sprint2/data/larkspur/kb/**` and `qa_gold.jsonl`. It adds `title` from each article's first heading, keeps `doc_id`, `section` and `updated`, maps `product` values, and keeps 24 articles and the dev questions whose evidence falls in those articles. The M1 code runs on this data. Its results check the code only; they are not MILO results and are never quoted as such. Larkspur's own `suggested_min_score` and recorded candidate recall serve as a cross-check.

## 4. Retrieval (M1, M2)

- Contract-mode turn: `validate` → `policy(input)` → `retrieve` (dense, top 5, cosine) → if no chunk clears the threshold, abstain with no model call → otherwise one model call with the context → answer.
- Threshold: calibrated on dev unanswerable rows in M1 and written into the report. The value 0.30 is a placeholder until calibration.
- `Source` list = the top 5 retrieved chunks, all of them. `Source.score` is cosine similarity.
- Context is wrapped in `<context chunk_id="...">…</context>` data blocks; the prompt says they are data.
- M1 metrics: recall@5 and nDCG@5 per question type, with bootstrap CI (D24). The report names the weakest type and the abstention rate on unanswerable rows.
- M2 (advanced mode, `MILO_ADVANCED_RAG=1`): D10 and D11 on, plus the hybrid arm (D8) as its own row. Paired bootstrap of the nDCG@5 difference against M1, dev only. Decision per arm: adopt, reject or inconclusive.
- Self-query fields: `section`, `product`, `updated_after`. Ingest stores `updated_num = int(YYYYMMDD)`; `updated_after` filters on it.

## 5. The turn (M5)

**Contract mode:** one turn = retrieval (section 4), then at most one model call that may return tool calls, then the tool loop (at most 4 model calls per turn in total), then `Response`.

**Discovery mode** (`create_app`), per owner message, as an LCEL chain:

1. `validate` (section 8) and `policy(input)`.
2. `extract`: model call 1. Returns JSON `{facts: [{field_id, value, unit, period, evidence_text}]}`. Pydantic validates it. Unknown field ids are dropped and traced.
3. `merge`: code checks type, range, unit and cross-field rules (for example daily volume against monthly). A new fact is `reported`. A value that contradicts a stored fact becomes `conflicting`, with both values kept. `confirmed` is set only when the owner's reply answers a confirmation question (code decides this from the protocol state).
4. `protocol`: the engine lists missing fields, conflicts and the next question (highest priority, not yet asked). Question text comes from the YAML.
5. `retrieve`: only when the owner asked about a module, process or price. Produces sources.
6. `reply`: model call 2. Acknowledges the facts recorded, asks the next question verbatim from the protocol, and explains retrieved material with its sources.
7. `estimate`: if the protocol state allows, the pricing engine runs (D39). The estimate goes to the panel, not into model text.

The model never sets a status, never writes a fact directly, and never decides that a field is complete. Model calls that must return JSON are checked for shape; a failed parse is retried once (D23b), then the turn returns a safe reply asking the owner to repeat the last point.

Protocol states (D39): `assessment_ready` (every `assessment` field is `reported`, `confirmed` or `estimated`); `quote_ready` (every `quote` field is `reported` or `confirmed`, and no conflict); `quote_blocked` (a blocking conflict, for example two daily volumes with no period).

## 6. Tools (M3)

| name | risk | parameters (JSON schema, `additionalProperties: false`) | behaviour |
|---|---|---|---|
| `search_knowledge` | read | `query` string 3 to 200 chars; `product` enum or null | Top chunks: `chunk_id`, `doc_id`, `score`, `text` |
| `get_session_spec` | read | none | Current facts with status and evidence, for the session's owner |
| `estimate_cost` | read | `module_ids` list of catalogue ids (max 6); `daily_request_volume` number or null | Pricing engine output: low, base, high, status, assumptions. The model does no arithmetic |
| `record_fact` | write | `field_id` enum from the protocol; `value` typed per field; `unit` or null; `period` or null; `evidence_text` string | Same merge path as step 3; denied for pricing fields when `taint=high` (D22) |
| `withdraw_submission` | destructive | `submission_id` integer | Needs approval (D48). Sets `status=withdrawn` on the owner's own revision; never deletes |

Tool outputs are wrapped as untrusted before the model sees them. There is no submit tool (D20). Contract mode exposes the same five tools; `record_fact` writes to an in-memory spec there.

## 7. The report (D40)

Ten sections, in order, each with evidence references:

1. **Summary**: one paragraph and the decision it supports.
2. **Company profile**: company and owner fields, with status.
3. **Current pipeline**: steps and channels as described.
4. **Diagnosis**: bottlenecks and error types, each marked reported or confirmed, with quoted owner words.
5. **Automation candidates**: a table scored on frequency, manual minutes, error impact, rule clarity, integration ease and exception complexity. Scores are computed in code; the narrative is model text that cites them.
6. **Recommended modules**: from the catalogue, each with its KB source.
7. **Cost estimate**: low, base, high; status; assumptions and exclusions; rate card version. Shown only when D39 allows a number, otherwise the reason and the missing fields.
8. **Simple return estimate**: `N × (t_manual − t_automated) × p_eligible / 60` hours per month, each input labelled reported, confirmed or assumed. No labour-cost savings are claimed.
9. **Unknowns and next steps**: missing and conflicting fields and the questions that would close them.
10. **Evidence appendix**: message ids, chunk ids, rate card version. Footer: indicative, non-binding, retention note (D43).

Canonical JSON in `submissions.report_json`; HTML rendered with Jinja autoescape.

## 8. Security (M4)

- **Input validation** (`security/validate.py`): strip first, then reject empty (`invalid_input:empty`). Length after strip 1 to 4000 (`invalid_input:too_long`). Reject control characters `\x00-\x08`, `\x0b`, `\x0c`, `\x0e-\x1f`, `\x7f` (`invalid_input:control_characters`). Tab, newline and carriage return are allowed. Uploads: MIME, 5 MB, UTF-8.
- Injection phrase patterns set `taint=high`. They do not block on their own.
- **Policy** (code only), (risk tier, taint) → decision:
  - `read` → `allowed`.
  - `write` → `allowed` for non-pricing fields; `denied` for pricing fields when `taint=high`.
  - `destructive` → `needs_approval` when `taint=low`; `denied` when `taint=high`.
  Every decision is traced.
- **Approval** (D48): a destructive call with `needs_approval` creates an `approvals` row bound to (session, turn, tool, argument hash), returns `needs_approval`, and stops. The owner approves or keeps it on screen. Resolves once, expires after 10 minutes, and only the session's owner can resolve it.
- **Identity**: the owner comes from the session. Argument keys `user_id`, `session_id`, `owner_id`, `thread_id` are removed. Every repository query takes the owner id from the session.
- **Submit gate**: consent recorded; protocol state allows a number or a revision (D39 `indicative` or `scenario`); CSRF token valid.
- **Measurement** (`evals/agent/run_attacks.py`), each attack checked with its `success_check`:
  - Attack success rate per risk tier (`read`, `write`, `destructive`), so the destructive number is not vacuous.
  - Rate of destructive calls attempted under injection, counted even when blocked.
  - A second arm with auto-approve switched on: shows that the taint rule still denies a destructive call under injection.
  - False refusal rate on benign tool cases (the tool cases with non-empty `expected_calls`).
  Report measured values, whatever they are.

## 9. Errors, logging, cost (M5)

- Every error inside `ask` maps to a D23 code. `Response.answer` is a plain message. No traceback reaches the response, the UI or `answer`; tracebacks go to the trace file, redacted.
- Trace (`logs/trace.jsonl`): `ts`, `thread_id`, `turn_id`, `node`, `duration_ms`, `status`, `error_code`, token counts, policy decisions. Customer text as hash and length.
- Model cost: `cost_usd = (input_tokens × price_in + output_tokens × price_out) / 1_000_000`, with prices from `chat_model.price_usd_per_mtok` (USD per million tokens). Local model price is `(0.0, 0.0)`.
- `Response.cost_usd` (model cost) is separate from the owner's estimate (D38). The UI labels both.
- `App.recent_traces(limit)` returns trace summaries for `/traces`. The web layer does not read the log file itself.

## 10. Evaluation and sealing

- `data/gold.jsonl` holds all 40 rows, because the kit checker needs the full file. Loaders in `evals/` filter by `split`.
- **Sealing rules:**
  - (a) No file under `src/` or `evals/`, except `evals/final/`, may compare `split` with `test` or read the gold file with a test filter. `tests/test_leakage.py` runs this as a static check.
  - (b) `evals/` loaders refuse `split=test` unless called from `evals/final/`.
  - (c) The final run appends one line to `evals/results/test_access.log`. The verifier fails the build if there is more than one line.
  - (d) F2 compares the SHA-256 of `data/gold.jsonl` with `MANIFEST.json` written at M0. A changed file fails F2.
- **Known limit, stated in the report:** agents can read the file, so sealing depends on the static check, the verifier and the log. It is not cryptographic.
- `test_leakage` also checks that no gold quote appears in any `evals/results/` file dated before the gold file's `updated` date. This answers M0 checkpoint question 2.
- **Result provenance:** every `evals/results/*.json` holds `git_commit`, `model`, `seed`, `temperature`, `timestamp`, `command`. Verifiers rerun the dev targets and compare within tolerance 0.005 on nDCG and recall.
- **Discovery evaluation** (`evals/discovery/`): six scripted owner conversations (fictional) with expected facts, statuses, next question ids and estimate status. Measures fact precision and recall, status accuracy, repeated-question rate and premature-estimate rate (target 0).
- M7 judge: `calibrate.py` reports Cohen's kappa with CI on the 30 labelled examples. `run_rag_eval.py` scores faithfulness, answer relevance and context precision on test answers, and runs only in F2.
- `make eval` runs offline from stored results and does not call a model.

## 11. Setup (S0, once, before any agent)

1. `pyproject.toml`: `[build-system]` with hatchling, `[tool.hatch.build.targets.wheel] packages = ["src/milo"]`, `[tool.uv] package = true`. Python 3.12.
2. Runtime: `uv add openai langchain-core langchain-openai flask werkzeug pydantic jsonschema chromadb rank-bm25 sentence-transformers pyyaml`.
   Dev: `uv add --dev pytest pytest-socket ruff mypy playwright types-PyYAML`.
3. Torch CPU wheels: `[tool.uv.sources] torch = { index = "pytorch-cpu" }` with the matching `[[tool.uv.index]]`. `uv sync` is a sanctioned download step.
4. mypy overrides: `[[tool.mypy.overrides]] module = ["rank_bm25.*", "sentence_transformers.*", "chromadb.*"] ignore_missing_imports = true`.
5. `pytest.ini`: `testpaths = tests`, `addopts = --disable-socket`, `pythonpath = src`, markers `tools`, `security`, `errors`, `sources`, `abstains`, `threads`, `cost`.
6. `Makefile`, all targets created as stubs in S0 and owned by the integrator. Kit targets `make kit-<keyword>` run `PROJECT_APP=milo.app:build_app uv run pytest contract -k <keyword> -rA`. Verifiers use only these targets. A bare `pytest contract` with no `PROJECT_APP` counts as a failure.
7. `tests/test_contract_target.py` asserts `os.environ["PROJECT_APP"].startswith("milo.")` under the kit targets. The toy app therefore cannot pass silently.
8. One-time downloads, each recorded with version and time in `docs/owner/CHECKPOINTS_PENDING.md`:
   - `uv run python -c "from sentence_transformers import SentenceTransformer as S; S('BAAI/bge-small-en-v1.5')"`
   - HTMX 2 and its SSE extension into `src/milo/web/static/vendor/`, with licence files and pinned versions chosen by S0. (Polling is used in v0.4, so the SSE extension is optional; S0 may skip it.)
   - `uv run playwright install chromium`
9. `.env.example`: `OLLAMA_BASE_URL`, `MILO_LIVE` (unset), `MILO_BUDGET_USD` (`0`), `MILO_DB`, `MILO_SECRET_KEY` (no value), `MILO_ADVANCED_RAG` (`0`), `ANONYMIZED_TELEMETRY=False`.
10. S0 ends with a minimal `build_app` that abstains on empty retrieval, so `make kit-abstains` is runnable, and a `create_app` stub.

## 12. Integration and file ownership

- Each agent works in its own worktree (D29) on `agent/<role>/<milestone>`, branched from `main` at the start of its phase.
- The integrator merges in section 13 order, runs the kit targets for the merged milestones and `make check` after each merge, and reverts any merge that breaks a green test.
- Every path has exactly one owner (HARNESS appendix A). Shared paths are owned by the integrator: `pyproject.toml`, `Makefile`, `pytest.ini`, `SPEC.md`, `README.md`, `src/milo/errors.py`, `src/milo/app.py`, `src/milo/types.py`. Owners send requests in `docs/requests/`.
- Size limit 2 MB per file, with exceptions for `docs/m6_recording.webm` (25 MB) and the vendored HTMX files.

## 13. Phases and parallelism

- **Phase 0 (serial):** S0 (section 11).
- **Phase 1a (parallel):** M0 data (KB, gold, cases, attacks, rate card, protocol); M3 tools and pricing engine (fakes and the S0 stub retriever); M5a LLM client, retries, errors; M6a repositories and auth; M6b web shell (stub App).
- **Phase 1b (after M0 merged):** M1 retrieval (dense only, dev split).
- **Phase 2 (parallel):** M4 security (after M3); M5b protocol, facts, chain, turns, approvals (after M1, M3, M5a); M2 advanced RAG (after M1).
- **Phase 3 (parallel):** M5c report (after M5b and pricing); M6c UI wired to the real App and the operator view (after M5b); M7 evaluation (after M2, dev only).
- **Phase 4 (serial):** final steps F1 to F4.

Verifiers run after each owner agent. A failed done-line gets up to 3 fix cycles, then the milestone is **blocked** and independent work continues.

## 14. Known conflicts with the kit

- Kit README says `cd sprint2/project-kit`; the real kit is at `../../../project-kit`. MILO copies the kit files at S0.
- Kit contract tests run in **contract mode** (D2). Discovery is the product mode and is tested separately. The owner confirms this split before the grade is reported.
- Kit `RUBRIC.md` says "Four criteria" and has three tables. The reflection follows the three tables.
- Kit M6 and README say Streamlit or Next.js. The owner accepted Flask (D13). The deviation is recorded in the M6 report and the README; confirm with the course before M6 is marked complete.
- Kit M5 names `ChatOpenAI` with a base URL. D4 uses that shape; the build never calls it.
- Kit `thread_id` maps to the owner's single session (D35). Kit `threads` checks that two owners never share history.
- Kit `abstains` forbids any model call on empty retrieval. Discovery's extraction call therefore runs only in discovery mode.
- The AE privacy check fails on notebooks in `sprint2/part2` and `sprint2/part4` (section 19).

## 15. Database

- `users(id, email UNIQUE, password_hash, role CHECK IN ('owner','operator'), created_at, last_login_at, failed_logins, locked_until)`
- `invites(token_hash UNIQUE, email, created_by, expires_at, used_at)`
- `customers(user_id PK→users, company_name, industry, employee_count, locations, owner_name, owner_role, owner_email, consent_at, consent_version)`
- `sessions(id PK, user_id UNIQUE→users, status CHECK IN ('active','submitted'), created_at, updated_at, last_seen_at)`
- `messages(id PK, session_id→sessions, role CHECK IN ('owner','assistant','system'), content, created_at, trace_hash)`
- `turns(id PK = turn_id hex, session_id→sessions, status CHECK IN ('running','done','error'), created_at, finished_at)`
- `turn_events(id PK, turn_id→turns, seq, type, payload_json, created_at)`, `UNIQUE(turn_id, seq)`
- `facts(session_id→sessions, field_id, value_json, unit, period, status CHECK IN ('reported','confirmed','estimated','unknown','conflicting'), source_message_id→messages, updated_at, PRIMARY KEY(session_id, field_id))`
- `fact_history(id PK, session_id, field_id, value_json, status, source_message_id, changed_at)`, append-only
- `evidence(id PK, session_id, message_id NULL, kind CHECK IN ('customer_statement','uploaded_document','kb_chunk','calculation'), ref, score, text_hash, created_at)`
- `submissions(id PK, session_id→sessions, revision, submitted_at, status CHECK IN ('received','report_ready','failed','withdrawn'), spec_snapshot_json, report_json, report_html, estimate_status, cost_low, cost_base, cost_high, currency, rate_card_version, model_id)`, `UNIQUE(session_id, revision)`
- `approvals(id PK, session_id→sessions, turn_id, tool_name, args_hash, status CHECK IN ('pending','approved','kept','used','expired'), created_at, expires_at)`
- `audit_log(id PK, actor_user_id NULL, action, target, detail_json, at)`: submits, approvals, withdrawals, login failures, operator views, deletions
- `store/schema.sql` is the single source of the schema, written in S0. Repositories enforce the owner filter. `tests/test_identity.py` checks that every owner-facing repository call fails for another owner's ids.

## 16. Front end (M6)

**Stack (D13):** Flask app factory `create_app(...)`, Jinja templates, one stylesheet with tokens as `:root` variables, light and dark via `prefers-color-scheme`, vanilla JavaScript, no CDN. Turn events are polled (D47). No token streaming: the answer appears whole.

**Look (D45):** colours and icon style come from the owner's reference. The layout does not.

| Role | Value | Used for |
|---|---|---|
| Canvas | `#F7F7F7` | page background |
| Surface | `#FFFFFF` | cards, top bar, panels |
| Ink | `#000022` (buttons `#01041C`) | primary button, headings |
| Selected tint | `#F3F4FD` | selected item, hover, focus background |
| Accent | `#5C1AF7` | MILO mark, links, progress, "reported" |
| Amber | `#FDB40E` (favourite `#FCAA00`) | lightning badges on suggestions, "estimated" |
| Green | `#40A178` | "confirmed", saved indicator |
| Muted | `#909090` | secondary text, "unknown" |
| Conflict (added) | `#D64545` | "conflicting", blocking messages |

Icons: thin rounded line icons, 1.5 to 2 px stroke, round caps and joins, 20 px in a 32 px rounded square. Suggestion rows: white, radius 12 px, amber bolt badge, chevron. Status is a coloured dot plus a text label, never colour alone. The MILO mark is a soft sphere (white to pale lavender, purple rim), shown only on the empty conversation. Radii: 12 px cards, 8 px buttons, 999 px pills. Text: system sans; headings 28 px semi-bold; body 15 px.

Layout (MILO's own):

```text
+-------------------------------------------------------------------+---------------------------+
| (mark) MILO                                   Saved 10:42  Dana   | Spec & evidence           |
+-------------------------------------------------------------------+ 14 of 24 fields           |
|                       (sphere mark)                               | =============---------   |
|                    Hello, Dana                                    |                           |
|           Tell us about your business.                            | Company                   |
|                                                                   |  Industry  Retail   ✓     |
|   [ Describe your business...............................  Send ] |  Employees 12   reported  |
|   (⚡) Tell us about your busiest manual process                  | Workflow                  |
|   (⚡) Share the tools you use today                              |  Channels  phone          |
|   (⚡) Ask what it would cost                                     | Missing: volume, ...      |
|                                                                   | [ Submit specification ]  |
+-------------------------------------------------------------------+---------------------------+
```

- No sidebar. Top bar: mark, save indicator, first name, menu (reports, sign out).
- Centre: the conversation. The empty state shows the greeting and suggestions; messages replace them.
- Right panel: always visible on wide screens; below the conversation under 900 px.

**Screens**

- **Conversation:** messages; inline evidence chips under each assistant reply (`2 sources`, `1 fact recorded`). A chip opens its evidence in the panel.
- **Spec and evidence panel:** progress, grouped fields with status, the quote behind each value, missing fields, and Submit (disabled until D39 allows, with the reason shown).
- **Submit dialog:** states that a revision is stored and cannot be edited.
- **Reports:** the owner's submissions; each opens the report (section 7).
- **Operator** (`/ops/`, role `operator`): submissions, spec snapshot, evidence, report, audit log, export.
- **Sign-in and invite:** plain labelled forms.

**Routes**

| Method and path | Purpose | Response |
|---|---|---|
| `GET /` | Conversation (creates the session on first visit) | page |
| `POST /messages` | Owner message; validated; starts a turn | JSON `{turn_id}` |
| `GET /turns/<turn_id>/events?after=<seq>` | Turn events | JSON list |
| `POST /upload` | Attach a document (D46) | JSON |
| `POST /submit` | Confirm and store a revision; builds the report | redirect to report |
| `GET /reports`, `GET /reports/<submission_id>`, `GET /reports/<submission_id>/download` | Owner's reports (own only) | page or file |
| `POST /approvals/<approval_id>` with `decision=approve` or `keep` | Resolve (own only) → `App.resume_approval` | JSON |
| `POST /withdraw/<submission_id>` | Starts the withdrawal approval (own only) | JSON |
| `GET /login`, `POST /login`, `POST /logout` | Sign in; sign out ("save and leave") | page or redirect |
| `GET /invite/<token>`, `POST /invite/<token>` | Set password from an invite | page |
| `GET /traces` | Trace summaries via `App.recent_traces` (operator only) | page |
| `GET /ops/submissions`, `GET /ops/submissions/<id>`, `GET /ops/export/<id>.json` | Operator only | page or file |

**Turn events** (`turn_events.type`): `node_started`, `node_finished`, `fact_recorded`, `source`, `policy`, `approval_required`, `estimate_updated`, `answer`, `error`, `done`.

**App contract additions** (owned by the integrator in `src/milo/app.py`): `App.ask(question, user_id, thread_id, on_event=None)`; `App.resume_approval(approval_id, decision, user_id)`; `App.recent_traces(limit)`. `on_event` receives a `types.Event` for each node.

**Web-layer security rules**

- Autoescape on. No `|safe` on any model, owner or document string. A test submits a script-tag payload and checks that it is escaped.
- Forms carry a hidden CSRF field; fetch calls send an `X-CSRF-Token` header. The token lives in the session.
- Session cookie: HTTP-only, `SameSite=Lax`, `Secure` when served over HTTPS.
- The secret key comes from `MILO_SECRET_KEY`.
- The web layer calls `App`, the repositories and the services only. It never imports retrieval, tools or security modules.
- Every page footer: "Indicative estimate. Not a quote. Non-binding."

**M6 checklist** (`docs/m6_checklist.md`): one tick box per screen and route. `evals/ui/record.py` runs headless Playwright against a live local server and ticks what the DOM shows. Items the DOM cannot show (keyboard use, WCAG contrast) are marked **verifier-manual**; the verifier checks contrast from the CSS tokens and runs a keyboard walk-through script. The recording covers: sign-in, messages, evidence, a fact recorded, a blocked action waiting for approval, submit, the report, sign-out, sign-in again with the conversation restored.

## 17. Milestones and done-lines

Each done-line uses a `make kit-<keyword>` target or a unit-test path. A milestone's done-line lists only what that milestone can make pass when merged on its own. Kit keywords that need later milestones run at F3.

| ID | Build (owned paths) | Done-line (verifier runs this) |
|---|---|---|
| S0 Scaffold | section 11 files; `src/milo/{__init__,types,errors,settings,spend,trace,app}.py` (minimal abstaining `build_app`, `create_app` stub); `contract/`; `scripts/`; `pytest.ini`; `Makefile`; `tests/conftest.py`; `tests/test_contract_target.py`; `store/schema.sql` | `uv sync`; `uv run ruff check`; `uv run mypy src/milo`; `make kit-abstains`; `uv run pytest tests/test_contract_target.py` |
| M0 Data | `data/**`, `protocols/discovery.v1.yaml`, `evals/agent/attacks_kb/`, `tests/test_checker.py`, `tests/test_leakage.py` | `python scripts/check_project_data.py data` prints `OK`; `uv run pytest tests/test_checker.py tests/test_leakage.py` pass; `MANIFEST.json` matches |
| M1 Retrieval | `src/milo/rag/{chunk,ingest,vectors,retrieve}.py`, `evals/retrieval/run_retrieval.py`, `tests/test_chunk.py`, `tests/test_vectors.py`, `docs/m1_report.md` | `uv run pytest tests/test_chunk.py tests/test_vectors.py` pass; `make eval-retrieval` reproduces the report within tolerance |
| M2 Advanced RAG | `src/milo/rag/{translate,selfquery,bm25,fusion,rerank}.py`, `evals/retrieval/ab.py`, `tests/test_selfquery.py`, `tests/test_fusion.py`, `docs/m2_report.md` | self-query fallback test passes; A/B table (rewrite, self-query, hybrid) with paired CI on dev; decision per arm |
| M3 Tools and pricing | `src/milo/tools/**`, `src/milo/pricing/**`, `src/milo/loop.py`, `evals/agent/run_tools.py`, `tests/test_tools.py`, `tests/test_pricing.py`, `docs/m3_report.md` | `uv run pytest tests/test_tools.py tests/test_pricing.py` pass; tool eval on 15 cases in the report |
| M4 Security | `src/milo/security/**`, `evals/agent/run_attacks.py`, `tests/test_policy.py`, `tests/test_identity.py`, `docs/m4_report.md` | `uv run pytest tests/test_policy.py tests/test_identity.py` pass; per-tier ASR, injection-attempt rate and the auto-approve arm reported |
| M5a LLM and errors | `src/milo/llm/**`, `tests/test_errors.py`, `tests/test_llm.py` (`errors.py` is integrator-owned; M5a sends requests) | `make kit-errors` passes against the M5a client in a stub App; `tests/test_llm.py` covers think stripping and the cost formula |
| M5b Protocol, facts, chain, turns, approvals | `src/milo/{protocol,facts,chain,turns,store,auth}/**`, `tests/test_protocol.py`, `tests/test_turns.py`, `evals/discovery/**`, `docs/m5_report.md`, `docs/traces/` (`app.py` is integrator-owned) | `uv run pytest tests/test_protocol.py tests/test_turns.py` pass; discovery eval reports premature-estimate rate; three traces saved |
| M5c Report | `src/milo/report/**`, `tests/test_report.py` | a report from a stored spec passes its test; every number traces to the pricing engine or a labelled input |
| M6 UI | `src/milo/web/**`, `evals/ui/record.py`, `tests/test_web.py`, `docs/m6_checklist.md`, `docs/m6_recording.webm` | `uv run pytest tests/test_web.py` passes (XSS and CSRF tests included); checklist ticked; recording produced; verifier-manual items recorded |
| M7 Evaluation | `evals/rag/**`, `docs/m7_report.md`, `docs/reflection.md` (`Makefile` `eval` target is integrator-owned) | `make eval` runs offline and succeeds; kappa with CI; limits section filled |

**Final steps (integrator, once):**

- **F1** Integrate all branches into `main`.
- **F2** `make final-test` (from `evals/final/`), once: checks the gold hash, runs the test split, appends to `test_access.log`, writes results.
- **F3** Full kit run: `make kit-tools`, `make kit-security`, `make kit-errors`, `make kit-sources`, `make kit-abstains`, `make kit-threads`, `make kit-cost`, each with `PROJECT_APP=milo.app:build_app` and `-rA`; then `make eval`, the data checker, and the privacy check scoped to MILO (section 19).
- **F4** Update `docs/owner/CHECKPOINTS_PENDING.md` (integrator) and the README status line.

## 18. Open items

- Owner confirms the contract-mode / discovery-mode split (section 14).
- Owner decides the privacy-check question (section 19).
- Operator email delivery of reports is not built. Email needs owner approval.
- Rate card and clients are fictional. Real pricing is a business decision made outside MILO.
- The reference screenshot is used for colours and icon style only (D45). Preview before M6 starts.

## 19. Blocker for AE commits

- `scripts/privacy_check.py` (AE) must pass before any commit to the AE repo. It currently fails on notebooks in `sprint2/part2` and `sprint2/part4`, which no agent edits. The owner decides: fix those notebooks, or accept a MILO-scoped check for MILO commits. Until then MILO commits stay local and the AE pointer stays staged.
- `.privacy-terms` is missing. The owner creates it from `.privacy-terms.example`.
- Verifiers replace `$HOME` with `~` in anything they paste into `docs/verify/`.

---

## Version log

- v0.1 (2026-10-21): customer-support scope, D1 to D31.
- v0.2 (2026-10-21): front end decided (HTMX, layout D); section 16 added.
- v0.3 (2026-10-22): product changed to owner discovery: spec, pricing, report, submission, operator view. D34 to D46.
- v0.3.1 (2026-10-22): look taken from the owner's colour set and icon style only; layout is MILO's own, with no sidebar.
- v0.4 (2026-10-22): red-team round 1 applied.
  - Two modes (D2); ephemeral Chroma and SQLite in contract mode (D6, D14).
  - Dense-only M1, hybrid as an M2 arm (D7, D8).
  - Cost divided by 1e6 (section 9). Error format `code[:detail]` (D23). Retry deadline (D23a). Output hygiene (D23b).
  - Sealing rules and result provenance (section 10).
  - Setup: dependencies, package build, mypy overrides, `PROJECT_APP` kit targets, pytest config (section 11).
  - Ownership and phases (sections 12, 13).
  - Turn model with polling replaces SSE (D47); approvals resume (D48); login, CSRF and recording (section 16).
  - Done-lines use kit targets (section 17).
  - D18 corrected: `AE/scripts/spend.py` exists and reports 2.7736 USD remaining.
- v0.4.1 (2026-10-22): demo companies A and B, six scripted conversations, generation pipeline G0 to G8 and the Larkspur smoke test (section 3A, D49 to D51). Red-team round 2 on v0.4 still pending.
- v0.4.2 (2026-10-22): prose drafted in ChatGPT outside the pipeline (D52). Source files added: `data/source/catalogue.yaml`, `data/pricing/rate_card.v1.yaml`, `data/demo/companies/a.yaml` and `b.yaml`, prompts in `data/prompts/chatgpt-prompts.md`.
- v0.4.3 (2026-10-22): D52 changed: the M0 data agent drafts the prose instead of ChatGPT. Structured data is produced by `scripts/build_data.py` (M0-owned).
