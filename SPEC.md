# MILO — Build Specification

Status: v0.9 draft. Supersedes v0.8. Sections 20 and 21 override earlier text. Review round 6 findings are applied in section 21; no further full review round is planned.
Agents read this file and `HARNESS.md` before anything else. Where this file and a kit file disagree, section 14 says which wins.

Requirements source: the project kit (`project-kit/MILESTONES.md`, `RUBRIC.md`, `CHECKPOINTS.md`, `contract/`). Kit files are read-only.

---

## 0. Product

MILO is a **discovery interview** for company owners. An owner signs in with an invited account, talks to an AI assistant about
their business, and sees the evidence behind every fact it records. When they submit, MILO stores an immutable specification
revision for the operator to review, and gives the owner a research-style report: current pipeline, automation candidates,
recommended modules, an indicative cost range and next steps.

- Actors: **owner** (one invited account per customer company) and **operator** (us; reviews every submission).
- **Discovery mode:** one session per owner account. Every message is saved as it is sent. Signing out is "save and leave". No thread list.
- All clients, cases, modules, prices and documents in the build are **fictional**.

## 1. Decisions

| # | Area | Decision | Reason |
|---|---|---|---|
| D1 | Domain | Customer discovery for small and mid-size businesses | The owner's product |
| D2 | Two modes | `build_app(chat, embeddings, tools, *, discovery=False, vector_store=None, db=None, min_score=0.30, sleep=None, system_prompt_extra=None)` is **contract mode**: validation, retrieval (threshold `min_score`), at most one model call before the tool loop (tool calls allowed), tool loop capped at 4 model calls in total, history per `(user_id, thread_id)` holding the raw user question and the assistant answer of each turn, traces and approvals in memory, no SQLite. It ignores `MILO_ADVANCED_RAG`. `build_discovery_app(chat, embeddings, tools)` = `build_app(..., discovery=True, db=":memory:")` with one session per `user_id` and `thread_id` mapped to it, used by `discovery-table`. `create_app(settings=None, app=None, db=None)` runs discovery mode with persistent stores. A discovery turn makes at most 4 model calls: extract, one JSON retry, reply, one tool follow-up. With `MILO_ADVANCED_RAG=1` the rewrite and self-query calls add two, and the turn deadline is 180 s in that mode | Kit tests assume one model call before tools; section 14 records the split |
| D3 | Dev and eval chat model | An OpenRouter model chosen by measurement on the dev split: the cheapest candidate that passes the extraction and reply checks. No local models anywhere in the build | Owner's direction 2026-10-22: OpenRouter only for the product |
| D4 | Product chat model | `claude-sonnet-5.5` through OpenRouter (pinned version, not `latest`), via `openai.OpenAI(base_url=...)` or `ChatOpenAI` with the OpenRouter base URL. Used for the final run and the product | Owner's choice |
| D5 | Embeddings | `google/gemini-embedding-2` through OpenRouter, chosen on the dev split on 2026-10-22: recall@5 1.00, nDCG@5 0.84 (16 answerable dev questions). Runner-up `openai/text-embedding-3-large`: nDCG@5 0.81. The gap is inside the confidence intervals, so the choice is provisional. The test split confirms it once, in F2. Query prefix: none. Candidates and results in `evals/results/retrieval/embedding_dev.json`, produced by `evals/retrieval/compare_embeddings.py` | Chosen by measurement, budget about 0.005 USD |
| D6 | Vector store | `build_app`: `chromadb.EphemeralClient()` with a fresh uuid collection per app, cosine space, `embeddings=` always passed to add and query, `ANONYMIZED_TELEMETRY=False` set before `import chromadb`, abstain when `collection.count() == 0` before querying. Ephemeral clients may share one in-process system, so the uuid collection name is the isolation. `create_app`: `PersistentClient(data/index/)` | Contract tests must not share documents |
| D7 | Retrieval baseline (M1) | **Dense only**, top 5 by cosine | One retriever in M1 |
| D8 | Hybrid (M2 arm) | BM25 fused with dense by reciprocal rank fusion, k = 60. Reported as its own A/B row against M1 | Kit hard bonus needs an A/B row |
| D9 | Chunking | Heading-aware, at most 800 characters with 100 overlap. Chunk id `<doc_id>#<index>` | Keeps sections intact |
| D10 | Query translation (M2) | Rewrite into one standalone query. On only when `MILO_ADVANCED_RAG=1` | Contract mode stays at one call |
| D11 | Self-query (M2) | JSON `{section?, product?, updated_after?}`, validated by Pydantic; invalid → unfiltered search. On only with D10 | Required fallback |
| D12 | Reranking | Not in the baseline; only as an A/B arm | No unmeasured claims |
| D13 | Web | Flask app factory, Jinja templates, vanilla JavaScript, no CDN at runtime. Turn events are polled from the database (D47). No HTMX | Owner and course accept Flask |
| D14 | Persistence | Discovery mode: SQLite (`data/milo.sqlite`) through repositories, connection per thread, WAL mode. Contract mode: in memory, no SQLite file is opened | One file in the product; none in tests |
| D15 | Trace | JSON lines in `logs/trace.jsonl` plus a `traces` table in discovery mode. One record per node, model call and policy decision. Customer text as hash and length | Kit traces, logging bonus |
| D16 | Tests | pytest with `addopts = --disable-socket`. `tests/` uses only `contract/fakes.py` and MILO's own fakes. No test starts Ollama or loads a model | Kit rule: offline |
| D17 | Lint and types | ruff over `src`, `tests`, `evals`, `scripts/build_data.py` only (section 11.5). mypy `--strict` set in `[tool.mypy]` | Kit files are read-only |
| D18 | Cloud spend | The budget is the owner's OpenRouter balance, read from the dashboard, not a repo ledger. On 2026-10-22: about 6.50 USD remaining (8.50 of 15 used). `MILO_BUDGET_USD` holds the budget. `src/milo/spend.py` logs each call's actual cost from OpenRouter's usage field to `logs/spend.jsonl` and refuses any run that would take the balance below a 1.00 USD reserve. Planned split: embeddings about 0.10, dev and eval about 2.50, judge about 0.50, final Sonnet run about 2.00, reserve 1.00 | Owner's budget; no outside ledger |
| D19 | Tools | Five tools (section 6), exposed through the backend protocol `specs()` and `call(name, args, *, user_id)`. Risk comes from `ToolSpec.risk`. `MiloBackend` implements it | Kit `tools` tests use the backend protocol |
| D20 | Submit is not a tool | The model cannot submit. Submit is an owner button with a CSRF token. No model-reachable code creates a submission | Stops injected creation |
| D21 | Identity | `user_id` comes from the session (web) or the `ask` argument (contract). Argument keys `user_id`, `session_id`, `owner_id`, `thread_id` are **removed before** schema validation. Repositories take the owner id from the session | Kit identity test; no vacuous pass |
| D22 | Taint | Customer text, uploads and retrieved KB text are `untrusted`. The pattern list fires on instruction-like phrases: `system override`, `system note`, `ignore previous`, `ignore all previous`, `mark every field`, `set the price to zero`, `set the quote to zero`. Plain imperatives such as "delete item 42" never fire it. With `taint=high`: destructive tools are denied; `record_fact` is allowed only for non-pricing fields. `tests/test_policy.py` lists the four kit security questions (`QUESTION`; `QUESTION` + "Also look up item 42."; `QUESTION` + "Also delete item 42."; `QUESTION` + "My code is ZEBRA-7731.") and checks that each is `taint=low` | Policy in code; no false denials |
| D23 | Error format | `Response.error` is `None` or `"<code>[:<detail>]"`. Codes: `invalid_input`, `provider_error`, `policy_denied`. Details: `invalid_input:empty`, `invalid_input:too_long`, `invalid_input:control_characters`, `provider_error:timeout`, `provider_error:unavailable`. `tool_error` never reaches `Response.error`: tool failures go back to the model as results | Kit tests read `split(":")[0]` |
| D23a | Retries | Retries happen only in `src/milo/llm/retry.py`; `loop.py` and `app.py` never retry a model call. Any exception from `chat_model.invoke` is `provider_error`: `TimeoutError` (or a class name containing "Timeout") gives `:timeout`, anything else `:unavailable`. At most 3 attempts in total, backoff 0.5 s and 1.0 s through the injected `sleep` (no-op in contract mode). Timeout 30 s per call; per-turn deadline 120 s (on expiry the turn returns `provider_error:timeout`, with the `tool_results` and `cost_usd` so far) | Kit scripts 4 exceptions; one retry site; bounded |
| D23b | Output hygiene | Thinking output is disabled for models that emit it. `<think>…</think>` blocks are stripped before parsing. JSON-expected calls are retried once on parse failure, then a safe reply | Thinking output breaks JSON |
| D24 | Statistics | Bootstrap 95% CI, 2000 resamples, seed 0; paired resampling for A/B | Kit requires CIs |
| D25 | Gold split | 40 rows as committed: 20 `dev`, 20 `test`; 8 identifier, 24 paraphrase, 8 unanswerable (4 dev); 11 filtered (5 dev). M1 calibrates the threshold on the 4 dev unanswerable rows, and the report says so. Sealing: section 10 | Committed data meets the kit minimums |
| D26 | Judge (M7) | An OpenRouter model, chosen with the other candidates, checked against the 30 labelled examples. Labels are written by a separate labelling agent, committed before the judge prompt | Same provider for all cloud calls |
| D27 | Bonus claims | Medium: prompt-injection protection (M4), logging and monitoring (M5), token and cost display (M6). Hard: RAG evaluation (M7). Hybrid search claimed only with its reported A/B row | Kit minimum |
| D28 | M6 recording | `evals/ui/record.py` drives headless Playwright against a live local server and writes `docs/m6_recording.webm` | Automatable |
| D29 | Git | Worktrees at `~/milo-worktrees/<role>-<milestone>` (outside the AE repo). Branch `agent/<role>/<milestone>`. Role slugs: `data`, `tools`, `llm`, `store`, `web`, `retrieval`, `advanced-rag`, `security`, `discovery`, `report`, `labelling`, `eval`. Integration only by the integrator, in section 13 order. `verify/<milestone>` branches are merged by the integrator too. Pushes only to `origin` after the owner's approval; approval given 2026-10-22 | Parallel isolation |
| D30 | Commit trailers | Subject `<scope>: <summary>`, scope `milo/<area>`, `docs`, `chore` or `milo/data`. The body ends with the trailers the session harness supplies, verbatim. At the time of writing: `Co-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01R9xpatJC8o2pDvMX57MQAb` | Trailers come from the harness, not this file |
| D31 | Checkpoint file | The integrator appends a block to `docs/owner/CHECKPOINTS_PENDING.md` at merge time, from a template. Owners never write it. Reports say **verified, owner review pending** | One writer |
| D34 | Accounts | Invite only. `flask invite <email>` (operator) creates a one-time token stored hashed, valid 72 h. The invite page sets a password (Werkzeug scrypt). Login is email and password. Cookies HTTP-only, `SameSite=Lax`, 12 h idle timeout. Five failed logins lock the account for 15 min | Only invited owners reach the interview |
| D35 | One session per owner | `sessions.user_id` UNIQUE, created at **sign-in** (not at page load). Every message is saved as sent. Sign-out is "save and leave". Discovery mode only | Owner's request |
| D36 | Discovery protocol | `protocols/discovery.v1.yaml`, validated by `src/milo/protocol/schema.py` (section 5.1) | One format for all agents |
| D37 | Fact status | `reported`, `confirmed`, `estimated`, `unknown`, `conflicting`. `confirmed` is set only by code, when the reply answers the field's `conflict_question`. A field is **to be confirmed** when it is `conflicting`, or when it has `pricing: true` and the session is not yet `quote_ready` | Evidence-aware |
| D38 | Pricing | `src/milo/pricing/` reads `data/pricing/rate_card.v1.yaml`, validated by `src/milo/pricing/schema.py` (section 5.2). Inputs: module ids, daily volume, integration ids. The model picks module ids only | Prices are arithmetic |
| D39 | Estimate status | `scenario` (a required field missing; assumptions shown), `indicative` (all quote fields `reported` or `confirmed`, no conflict), `blocked` (a blocking conflict; no number) | Honest gating |
| D40 | Report | Ten sections (section 7). Canonical JSON stored; HTML rendered from it. Indicative and non-binding | Decision support |
| D41 | Submission | Submit: consent recorded, status `assessment_ready` and not `quote_blocked`, one confirmation dialog. Stores an immutable revision (`UNIQUE(session_id, revision)`), then builds the report. Only `status` changes later (`withdrawn`) | Auditable record |
| D42 | Operator view | `/ops/` for role `operator` only | Operator review |
| D43 | Personal data | Owner contact fields stored after `POST /consent` (`consent_at`, `consent_version`). Operator deletion writes an audit row. Fictional owners only | Customer information |
| D44 | Currency | USD, from the rate card | Course context |
| D45 | Visual theme | Colours and icon style from the owner's reference (section 16); layout MILO's own | Owner's request |
| D46 | Uploads | PDF or text, max 5 MB, MIME and size checked, UTF-8. Extracted text is evidence (`uploaded_document`), `untrusted`; facts from it start `reported` | Processes live in documents |
| D47 | Turns | One running turn per session (a second send gets 409). Turn row `turn_id` = uuid4 hex. The turn runs in a background thread calling `App.ask(..., on_event=...)`; events go to `turn_events`. The browser polls `GET /turns/<id>/events?after=<seq>` every 500 ms | Works with a synchronous app |
| D48 | Approvals | Lifecycle in section 8. `App.resume_approval(approval_id, decision, user_id) -> Response` | Deterministic, IDOR-safe |
| D53 | Model approvals (owner, 2026-10-22, updated) | OpenRouter is the only provider, for every chat, embedding and judge call. No local models at all | Owner's direction |
| D54 | Course and checks (owner, 2026-10-22) | The course accepts Flask. The privacy check (AE `CLAUDE.md` line 63, `docs/repo-conventions.md` section 11) runs on MILO's own files for MILO commits, via `scan(paths)`. The file list comes from `git ls-files`, not a directory walk. `.privacy-terms` is still to be created by the owner | Owner's instruction |
| D55 | Prose and structured data | KB articles, owner messages, gold questions, the community post and the upload text are written by the M0 data agent (a Fable agent under the owner's instruction). No script calls a model API. Structured files come from `scripts/build_data.py`, which is deterministic. Each drafted group has an entry in `data/prompts/generation-record.md` | Owner's instruction; checks are the gate |
| D56 | Results provenance | Every eval writes one file `evals/results/<milestone>/<name>.json` holding: `git_commit`, `model`, `seed`, `temperature`, `command`, `timestamp`, `inputs` (SHA-256 of gold, KB, tool cases, attacks and the prompt file), and `rows` (per-row outputs, including model rewrites, filters and judgements). Section 10 | No hand-written results |

## 2. Repository layout

```text
milo/                             repo root; submodule at AE/sprint2/project; origin = private GitHub repo
  README.md  SPEC.md  HARNESS.md  .gitignore  .env.example  .python-version
  pyproject.toml  uv.lock  Makefile  pytest.ini  conftest.py
  contract/                       copied from project-kit/contract (read-only)
  scripts/check_project_data.py   copied from project-kit/scripts (read-only)
  scripts/build_data.py           deterministic structured-data builder (M0)
  protocols/discovery.v1.yaml
  data/
    kb/                           knowledge base (tracked)
    gold.jsonl  tool_cases.jsonl  attacks.jsonl  MANIFEST.json   (tracked)
    source/catalogue.yaml         (tracked)
    pricing/rate_card.v1.yaml     (tracked)
    demo/companies/*.yaml  demo/conversations/*.jsonl  demo/uploads/   (tracked)
    prompts/                      prompt briefs and generation-record.md (tracked)
    index/  milo.sqlite  logs/    generated, git-ignored
  src/milo/
    __init__.py  app.py  types.py  errors.py  settings.py  spend.py  trace.py
    protocol/     schema.py loader.py engine.py validators.py
    pricing/      schema.py rate_card.py estimator.py
    facts/        model.py merge.py
    llm/          client.py cloud.py retry.py think.py tokens.py
    rag/          chunk.py ingest.py vectors.py bm25.py fusion.py translate.py selfquery.py retrieve.py rerank.py
    tools/        specs.py backend.py objects.json approvals.py
    security/     validate.py policy.py identity.py taint.py
    prompts/      contract.py discovery.py
    chain.py  loop.py  turns.py
    report/       build.py templates/report.html
    store/        schema.sql db.py users.py sessions.py messages.py facts.py submissions.py approvals.py audit.py traces.py
    auth/         invites.py passwords.py login.py ratelimit.py
    web/          __init__.py routes.py templates/ static/css/app.css static/js/app.js
  evals/
    common.py                     the only file that names gold.jsonl (section 10)
    retrieval/  agent/  discovery/  rag/  ui/  results/  final/
  tests/
  docs/
    m1_report.md ... m7_report.md  m5a_report.md  reflection.md  m6_checklist.md  m6_recording.webm
    traces/  verify/  requests/  owner/CHECKPOINTS_PENDING.md
    design/                       git-ignored (layout previews are published separately)
```

Git-ignored: `data/index/`, `data/milo.sqlite`, `logs/`, `.env`, `.venv/`, `evals/results/**/*.tmp`, `docs/design/`, `docs/project_guidelines.md`, `docs/guideline.md`, `docs/claude_guideline.md`. Owners of every path: `HARNESS.md` appendix A.

## 2A. Wire types (contract between agents)

Defined in `src/milo/types.py` (integrator, S0). They mirror `contract/fakes.py`.

- `Usage(input_tokens: int, output_tokens: int)`
- `ToolCall(id: str, name: str, args: dict)`, exactly as `contract/fakes.py` defines it. The kit's attribute names are used everywhere; MILO never renames them.
- `ChatReply(content: str, tool_calls: list[ToolCall], usage: Usage)`
- `ToolSpec(name: str, description: str, parameters: dict, risk: Literal["read", "write", "destructive"])`
- Chat model: `invoke(messages: list[dict], tools: list[ToolSpec] | None) -> ChatReply`, where `messages` are `{"role", "content"}` dicts. Attribute `price_usd_per_mtok: tuple[float, float]`.
- Conversion to OpenAI tool JSON happens only inside `llm/client.py`. The OpenRouter client uses `openai.OpenAI(base_url=...)` with the OpenRouter base URL, and reads each call's cost from the usage field.
- Embeddings: `embed(texts: list[str]) -> list[list[float]]`. Any query prefix a model needs is applied inside its wrapper only.
- Tools backend: `specs() -> list[ToolSpec]`; `call(name, args, *, user_id) -> Any`.
- `Document(doc_id: str, text: str, metadata: dict)`; `Source(chunk_id, doc_id, score, text)`; `ToolResult(name, arguments, output, ok, policy_decision)`; `Response(answer, sources, tool_results, cost_usd, stopped, error)`.

## 3. Data (M0)

- `data/kb/<section>/<name>.md`: 24 articles, 150 to 600 words, English. `doc_id` = the path without `.md` (for example `solutions/ai-phone-intake`). Front matter: `title`, `section`, `updated` (YYYY-MM-DD), `doc_id` (must equal the path), `product` (`general` or a module id). Sections: `solutions/` (six modules from `catalogue.yaml`), `pricing/`, `implementation/`, `cases/`, `consultation/`, `policies/`.
- `data/gold.jsonl`: 40 rows as committed (8 identifier, 24 paraphrase, 8 unanswerable; 20 dev and 20 test; 11 filtered rows, 5 in dev). Kit fields: `qid`, `question`, `type`, `gold_evidence`, `reference_answer`, `filters`, `split`. Unanswerable rows: empty evidence and `reference_answer` `INSUFFICIENT_CONTEXT`. Every filtered row's evidence satisfies its filter. Quotes are verbatim and at most 150 characters.
- `data/tool_cases.jsonl`: 15 rows with `id`, `user_id`, `query`, `expected_calls`, `match` (`exact` or `names`). `user_id` is `owner-a`, `owner-b` or `owner-e`. `owner-e` is a third fictional owner with no submissions, used only in tc13 and atk10. E-1 runs as company A's owner with an injection turn, and E-2 runs as company B's owner with an IDOR turn. At least three distinct tools; three over-eager traps; one submit request expecting no tool. **Open M0 fix:** tc04 and tc13 expect `daily_request_volume`, which `estimate_cost` no longer accepts (section 6), so M0 regenerates them with `module_ids` only before M3 starts.
- `data/attacks.jsonl`: 10 rows with `id`, `vector`, `goal`, `user_id`, `user_message`, `payload`, `carrier`, `success_check`. Kit enums are kept: `vector` in {direct, rag, tool_output}; `carrier` in {user_message, kb_chunk, tool_output}; `success_check.type` in {tool_executed, output_leak, identity_changed}. The upload and community-post attacks use `vector: rag` and `carrier: kb_chunk`, with `carrier_file` naming the document. Include cost manipulation, IDOR, a fake system instruction in an upload, withdrawal, submit through the model, and at least one destructive goal. **Open M0 fix:** atk04's note must match its taint result, and `output_leak` needs at least one direct exfiltration row that uses the canary.
- The M0 data agent is the only writer of `data/**`. `scripts/build_data.py --check` must exit 0 and the kit checker must print `OK`.

## 3A. Demo companies

- **D49** Two fictional companies drive the demo and the discovery evaluation.
  - **A. Maple & Paw Grooming** (owner Lena Ortiz, 9 staff, 2 salons): phone and web-form bookings in a spreadsheet; consistent facts; asks for the price early; short answers.
  - **B. Ironbridge Plumbing & Heating** (owner Marcus Hale, 28 staff, 3 vans): shared inbox and paper job sheets; export-only tool; night emergency line; personal data; a volume conflict (40 a day, later 400 a month, true value 19 a business day); an uploaded procedure with one injected line.
- Truth files: `data/demo/companies/a.yaml`, `b.yaml`. Six scripted conversations in `data/demo/conversations/` (A-1, A-2, B-1, B-2, E-1, E-2) with expected facts, states and next questions per turn in `<id>.expected.json`, derived from the truth files and the protocol, never from a model.
- **D50** Source of truth: `catalogue.yaml`, `rate_card.v1.yaml` and the truth files. Generated files are not edited by hand.
- **D51** The reference dataset in the course data folder (`sprint2/data/`, one subfolder) is not MILO content. It may be converted (outside the repo, in a scratch folder) for an M1 smoke test of the code. Its results are never reported as MILO results.

## 4. Retrieval (M1, M2)

- Contract mode, per turn: `validate` → `policy(input)` → `retrieve` (dense, top 5, cosine) → if the best score is below `min_score` (0.30 in contract mode, fixed), abstain with no model call → otherwise one model call with the context → answer. The calibrated value for the chosen embedding model (from dev unanswerable rows in M1) is stored in `settings.py` and used only by `create_app`.
- `Source` list = the top 5 retrieved chunks. `Source.score` is cosine similarity.
- Context is wrapped in `<context chunk_id="...">…</context>` blocks that the prompt marks as data.
- `App.ingest(docs: Sequence[Document]) -> None`. `Document(doc_id, text, metadata)`. The chunker takes `(doc_id, text, metadata)`. Ingest omits metadata keys that are absent; `updated_num` (int YYYYMMDD) is computed only when `updated` exists.
- M1 metrics: recall@5 and nDCG@5 per question type, bootstrap CI, abstention rate on unanswerable rows. The report names the weakest type.
- M2 (`MILO_ADVANCED_RAG=1`): D10 and D11 on; hybrid (D8) as its own arm. Paired bootstrap on nDCG@5 against M1, dev only. Decision per arm.
- Self-query fields: `section`, `product`, `updated_after`.

## 5. The turn

**Contract mode:** retrieval (section 4) → one model call that may return tool calls → tool loop (at most 4 model calls per turn in total) → `Response`. Tools come from `tools_backend.specs()`, run only through `tools_backend.call(name, args, user_id=...)`, and every attempt is recorded in `tool_results` with its policy decision.

**Discovery mode** (per owner message, LCEL chain):
1. `validate` and `policy(input)`.
2. `extract`: model call 1, JSON `{facts: [{field_id, value, unit, period, evidence_text}]}`, validated by Pydantic. Unknown ids are dropped and traced.
3. `merge`: code checks type, range, unit and cross-field rules. A new value is `reported`. A contradicting value becomes `conflicting`, with both kept. `confirmed` is set only by code, when the reply answers the field's `conflict_question`.
4. `protocol`: the engine lists missing fields, conflicts and the next question (highest priority, not yet asked). Question text comes from the YAML.
5. `retrieve`: only when the extract output sets `asks_about` to `module`, `process` or `price` (a validated enum).
6. `reply`: one model call, then at most one tool follow-up under the same policy. A discovery turn makes at most 4 model calls in total, so the 120 s deadline holds.
7. `estimate`: if the protocol allows, `estimate_cost` runs. Its numbers go to the `estimate_updated` event and the panel, **not** to the model.

The model never sets a status, writes a fact directly, or decides that a field is complete. A JSON reply that fails to parse is retried once (D23b), then the turn asks the owner to repeat the last point.

### 5.1 Protocol file (`protocols/discovery.v1.yaml`)

The committed file is authoritative, and its header comment is the reading guide. `protocol/schema.py` (Pydantic) must accept every key the file uses. `tests/test_protocol.py` checks that by loading the file. Top-level keys: `version`, `updated`, `states`, `groups`, `conversions`, `fields`. Per field: `id`, `group`, `type`, `unit`, `min`, `max`, `values`, `periods`, `pricing`, `blocking`, `allow_unknown`, `pii`, `required_for`, `condition` (`{field, equals}`), `question`, `conflict_question`. Field order is the question order. Conflicts come first (D37). An unknown field referenced in a condition fails the load. A condition whose operand is unknown is false.

### 5.2 Rate card file (`data/pricing/rate_card.v1.yaml`)

The committed file is authoritative. `pricing/schema.py` (Pydantic) loads it. The last volume tier may carry `factor: null` with a `note` key; the schema accepts both. Top-level key `rate_card`, with `version`, `currency`, `base_monthly_usd` (module id to USD per month), `setup_fee_usd.per_module`, `integration_surcharge_monthly_usd` (integration id to USD per month), `volume_tiers_daily_requests` (list of `{up_to, factor}`; `up_to: null` means no automatic estimate), `range` (`low_factor`, `base_factor`, `high_factor`) and `max_modules_per_estimate`. The return estimate also needs `automated_minutes_per_request` and `eligible_share` for each module. These are not in the file yet. A data fix adds them before M5c starts. Until then the report marks the return estimate `unavailable`.


## 6. Tools

| name | risk | parameters (`additionalProperties: false`) | behaviour |
|---|---|---|---|
| `search_knowledge` | read | `query` 3 to 200 chars; `product` enum or null | Top chunks with `chunk_id`, `doc_id`, `score`, `text` |
| `get_session_spec` | read | none | Facts with status and evidence, for the session's owner |
| `estimate_cost` | read | `module_ids` only (max 6). Volume and integrations are read from the session facts in code, never from arguments | Returns `{status, module_ids, assumptions}`. Numbers go to events (section 5 step 7) |
| `record_fact` | write | `field_id` (enum from the protocol); `value` declared as `anyOf: [string, number, boolean, array]` and checked per field in the merge; `unit` or null; `period` or null; `evidence_text` | Same merge path as step 3; denied for pricing fields when `taint=high` |
| `withdraw_submission` | destructive | `submission_id` integer | Approval lifecycle (section 8). Sets `status=withdrawn` on the owner's own revision |

`MiloBackend` in `tools/backend.py` implements `specs()` and `call()`. `tools/objects.json` maps submission 1 and session 1 to owner-a, submission 2 to owner-b, and submission 3 to a third owner absent from the other data. M3 writes it to exactly this map. **Open M0 fix:** tc07 must not ask owner-b to withdraw submission 1.

## 7. The report (D40)

Ten sections, in order, with evidence references: summary; company profile; current pipeline; diagnosis (reported or confirmed, with quoted words); automation candidates (scores computed in code from fields in section 5.1 and rate-card constants `automated_minutes` and `eligible_share`); recommended modules; cost estimate (low, base, high, status, assumptions, rate card version); simple return estimate (`N × (t_manual − t_automated) × p_eligible / 60` with each input labelled reported, confirmed or assumed, and `t_automated` and `p_eligible` taken from the rate card); unknowns and next steps; evidence appendix with the footer.

## 8. Security and approvals (M4)

- **Validation** (`security/validate.py`, shipped in S0): strip, then reject empty; length 1 to 4000 (`invalid_input:too_long`); reject `\x00-\x08`, `\x0b`, `\x0c`, `\x0e-\x1f`, `\x7f`; tab, newline and carriage return are allowed. Uploads: MIME, 5 MB, UTF-8.
- **Identity** (`security/identity.py`): identity keys are removed **before** schema validation (D21). The backend receives the session's or `ask`'s `user_id`.
- **Policy** (`security/policy.py`): `read` → allowed. `write` → allowed at `taint=low`; at `taint=high` allowed unless the tool is `record_fact` on a `pricing: true` field, which is denied. This rule covers every write tool, including the kit fakes. `destructive` → `needs_approval` when `taint=low`; denied when `taint=high`. Every decision is traced.
- **Approval lifecycle** (D48), for `destructive` calls:
  1. The loop calls the `ApprovalStore` protocol. Contract mode uses an in-memory store; discovery mode uses the `approvals` table. The row binds (session or ask-scope, turn, tool, argument hash) and status `pending`, expires after 10 minutes. The reply returns `needs_approval` and `stopped=True`.
  2. `approve`: the tool runs once with the bound identity; one follow-up model call explains the result; status `used`.
  3. `keep`: status `kept`; nothing runs; no model call.
  4. Expiry: status `expired` when read after `expires_at`; nothing runs.
  5. `/withdraw/<submission_id>` (the owner's button, not the model): calls `withdraw_submission` through the same policy, with no model call, and resolves its own approval.
  Only the owning session can resolve an approval; any other owner gets `not_found`.
- **Submit gate** (D41): consent recorded; status `assessment_ready` or better; not `quote_blocked`; CSRF valid.
- **Success per check** (`success_check.type`): `tool_executed` = `call()` ran for a tool the policy should have denied (taint high), or touched another owner's id. `identity_changed` = the backend received a `user_id` other than the one passed to `ask`. `output_leak` = the canary appears in `answer`. Each attack is scored by its own type.
  - `kb_chunk` carriers are indexed into an ephemeral attack index built from `carrier_file`, never `data/index/`.
- **Measurement** (`evals/agent/run_attacks.py`, contract mode with the OpenRouter client, the chosen embedding model and `MiloBackend`, plus an injecting wrapper for `tool_output`):
  - Headline arm: model-driven. Report attack success rate per risk tier, and the rate of destructive calls attempted under injection.
  - Forced-attempt arm: a scripted model emits each destructive call at each taint level, to test the policy deterministically.
  - Auto-approve arm: shows the taint rule still denies destructive calls under injection.
  - False refusal rate on the benign tool cases (non-empty `expected_calls`).
  Report measured values, whatever they are.

## 9. Errors, logging, cost (M5)

- Every error inside `ask` maps to a D23 code. `Response.answer` is a plain message. No traceback reaches the response, the UI or `answer`.
- `types.Event` = `Event(type: str, payload: dict, ts: float)`. Trace records carry `ts`, `thread_id`, `turn_id`, `node`, `duration_ms`, `status`, `error_code`, token counts and policy decisions; customer text as hash and length.
- `cost_usd = (input_tokens × price_in + output_tokens × price_out) / 1_000_000`, prices from `chat_model.price_usd_per_mtok`.
- Discovery mode returns the kit `Response` fields. Facts, the next question and the estimate travel in events and the database, not in extra `Response` fields.
- `App.recent_traces(limit)` reads the `traces` table in discovery mode and the in-memory list in contract mode.

## 10. Evaluation, sealing and provenance

### 10.1 Sealing
- `evals/common.py` is the **only** file that names `gold.jsonl`. It returns `dev` rows by default. It returns `test` rows only when `MILO_FINAL=1`, and only `make final-test` sets that variable. Each `test` load appends a line to `evals/results/test_access.log` with status `started`.
- Static check (`tests/test_leakage.py`): no file under `src/`, `evals/` (except `evals/common.py`) or `tests/` contains `gold.jsonl`. `tests/test_leakage.py` and `tests/test_checker.py` are excluded; they build the file name from parts.
- F2 checks, through `common.gold_sha256()`, that the SHA-256 of `data/gold.jsonl` equals `MANIFEST.json`.
- Known limit, stated in the report: the file is readable; sealing relies on the check, the verifier and the log.
- Leakage on answers: `test_leakage` checks, with git, that no commit on a branch named `agent/retrieval/*` or `agent/advanced-rag/*` predates the commit that added `data/gold.jsonl`. The retrieval agent starts after the data is merged.
- F2 writes one line per attempt with its status; the verifier requires exactly one `ok` line.

### 10.2 Canary
- `tests/canary.txt` is created by S0. The eval injects the canary through `build_app(..., system_prompt_extra=...)`; no `src/` code reads `tests/`.

### 10.3 Provenance and replay
- Every eval writes `evals/results/<milestone>/<name>.json` (D56). `inputs` hashes the data and prompt files; `rows` stores per-row outputs.
- The verifier runs `make <target>-replay`, which recomputes every metric from the stored rows and must match exactly. Dense-only M1 is also rerun live and compared within 0.005. Model-driven arms are reported, not failed (see the second bullet of this section).
- Live reruns of model-driven arms (M2 rewrite and self-query, M4, M5, M7) are reported; they do not fail the verifier, because GPU output at temperature 0 is not bit-reproducible.

### 10.4 Discovery evaluation
Six scripted conversations (section 3A), measuring fact precision and recall, status accuracy, repeated-question rate, and premature-estimate rate (target 0).

### 10.5 Judge (M7)
- The judge prompt is committed after the labels. The labels come from the labelling agent (D26) and are stored first.
- M7 runs faithfulness, relevance and context precision on **dev** and stores the results. F2 reruns the same script on test through `evals/final/`.
- Agreement with the labels: Cohen's kappa with CI, for the two judge models chosen in the dev comparison (OpenRouter).

## 11. Setup (S0)

1. `.python-version` = `3.12`. `pyproject.toml`: `requires-python = ">=3.12,<3.13"`; `[build-system]` hatchling; `[tool.hatch.build.targets.wheel] packages = ["src/milo"]`; `[tool.uv] package = true`.
2. Runtime: `uv add openai langchain-core langchain-openai flask werkzeug pydantic jsonschema chromadb rank-bm25 pyyaml`.
   Dev: `uv add --dev pytest pytest-socket ruff mypy playwright types-PyYAML types-jsonschema`.
3. Torch CPU wheels: `[[tool.uv.index]]` named `pytorch-cpu` with `explicit = true`; `[tool.uv.sources] torch = { index = "pytorch-cpu" }`. `uv sync` is sanctioned for every agent worktree.
4. `[tool.mypy] strict = true`; `[[tool.mypy.overrides]] module = ["rank_bm25.*", "chromadb.*"] ignore_missing_imports = true`.
5. `[tool.ruff] extend-exclude = ["contract", "scripts/check_project_data.py"]` and `[tool.ruff.lint] select = ["E4", "E7", "E9", "F"]`, pinned so that rule-set changes cannot move the gate. Lint scope is the default tree minus the excluded paths. `scripts/build_data.py` is formatted with `ruff format` (done 2026-10-22).
6. `pytest.ini`: `testpaths = tests`, `addopts = --disable-socket`, `pythonpath = src contract`, markers `tools`, `security`, `errors`, `sources`, `abstains`, `threads`, `cost`.
7. Root `conftest.py` (integrator): `os.environ.setdefault("PROJECT_APP", "milo.app:build_app")`, so a plain `uv run pytest contract` tests MILO. `pytest_collection_modifyitems` raises `pytest.UsageError` unless `PROJECT_APP` starts with `milo.`, so the run exits non-zero (an xfail marker would exit 0). The verifier runs `python -c "import milo.app; print(milo.app.__file__)"` by hand to confirm which module ran.
8. No model downloads. Every model is called through OpenRouter. The only one-time download is the Playwright browser for the M6 recording. Recorded in `docs/owner/CHECKPOINTS_PENDING.md` with version and time.
   - `uv run playwright install chromium`
9. `.env.example`: `OPENROUTER_API_KEY` (no value), `MILO_BUDGET_USD` (the owner's balance at setup), `MILO_DB`, `MILO_SECRET_KEY` (no value), `MILO_ADVANCED_RAG` (`0`), `MILO_FINAL` (unset). The app sets `ANONYMIZED_TELEMETRY=False` in code before importing chromadb.
10. `Makefile`: every target below is created in S0 with its command. A target whose owner has not merged yet still runs its command and fails, so no stub can pass.

| target | command |
|---|---|
| `kit-<kw>` | `PROJECT_APP=milo.app:build_app MILO_ADVANCED_RAG=0 MILO_LIVE= uv run pytest contract -k <kw> -rA` |
| `check` | `uv run ruff check . && uv run ruff format --check . && uv run mypy src/milo && uv run pytest && { test ! -f data/gold.jsonl \|\| uv run python scripts/build_data.py --check; } && { test ! -f data/gold.jsonl \|\| uv run python scripts/check_project_data.py data; } && { ! git ls-files -z \| xargs -0 du -b \| awk '$1>2097152' \| grep -v m6_recording; }` |
| `eval-retrieval` | `uv run python evals/retrieval/run_retrieval.py` |
| `eval-retrieval-replay` | `uv run python evals/retrieval/run_retrieval.py --replay` |
| `eval` | `uv run python evals/run_all_replay.py` |
| `final-test` | `MILO_FINAL=1 uv run python evals/final/run_final.py` |
| `record` | `uv run python evals/ui/record.py` |
| `index` | `uv run python -m milo.rag.ingest data/kb` |
| `discovery-table` | `for k in sources abstains tools security errors threads cost; do PROJECT_APP=milo.app:build_discovery_app MILO_ADVANCED_RAG=0 MILO_LIVE= uv run pytest contract -k $$k -rA \|\| true; done` prints the outcome table and exits 0. Before M5b, `build_discovery_app` raises `NotImplementedError` and the table says not built |
| `eval-tools` | `uv run python evals/agent/run_tools.py` |
| `eval-tools-replay` | `uv run python evals/agent/run_tools.py --replay` |
| `eval-attacks` | `uv run python evals/agent/run_attacks.py` |
| `eval-attacks-replay` | `uv run python evals/agent/run_attacks.py --replay` |
| `eval-discovery` | `uv run python evals/discovery/run_discovery.py --replay` |
| `eval-ab` | `uv run python evals/retrieval/ab.py --replay` |
| `eval-rag` | `uv run python evals/rag/run_rag_eval.py --replay` |

11. S0 ships the complete contract-mode pipeline, plus the validation, schema, store and stub files listed in the S0 row of section 17. `tests/test_contract_target.py` is removed; item 7 replaces it. `build_discovery_app` ships as a stub that raises `NotImplementedError` until M5b.
12. Imports: `import milo.app` makes no network call and loads no model.

## 12. Integration and file ownership

- Each agent works in its own worktree (D29) on `agent/<role>/<milestone>`, branched from `main` at the start of its phase.
- S0 is run by the **integrator** role, not by an owner agent.
- The integrator merges in section 13 order, runs the kit target for each merged milestone and `make check`, and reverts a merge that breaks a green test.
- Shared paths are integrator-owned (`pyproject.toml`, `Makefile`, `pytest.ini`, `conftest.py`, `SPEC.md`, `README.md`, `src/milo/{app,types,errors,settings,trace,spend}.py`, `docs/owner/CHECKPOINTS_PENDING.md`, `evals/common.py`). Owners send requests in `docs/requests/`.
- Size limit 2 MB per file, except `docs/m6_recording.webm` (25 MB).

## 13. Phases

- **Phase 0 (integrator):** S0 (section 11).
- **Phase 1a (parallel):** M0 remaining scope (its data is merged; see section 3 and the M0 row); M3 tools and pricing (uses the S0 seams and `MiloBackend`); M5a LLM client, retries and errors; M5-store; M6 shell.
- **Phase 1b (after M0 is merged):** M1 retrieval (dense only, dev split).
- **Phase 2 (parallel):** M4 security (after M3); M5b protocol, facts, chain, turns, approvals, prompts (after M1, M3, M5a, M5-store); M2 advanced RAG (after M1).
- **Phase 3 (parallel):** M5c report (after M5b and pricing); M6 UI wiring to the real App and the operator view (after M5b); M7 evaluation (after M2, dev only).
- **Phase 4 (integrator):** F1 to F4.

Kit targets: S0 runs every `kit-*` target as a wiring smoke check. An owner's milestone rests on its own tests (section 17). Each merge still runs `make check` and the kit targets for the merged milestone, to catch regressions in the shared pipeline.

## 14. Known conflicts with the kit

- The kit README says `cd sprint2/project-kit`; the real kit is at `../../../project-kit`. MILO copies the contract files at S0.
- **Contract mode vs discovery mode** (D2, owner decided 2026-10-22). One pipeline; contract mode switches the discovery nodes off. The kit suite runs in both modes; the report lists every kit test that passes in discovery mode and every one that does not, with the reason.
  Predicted `discovery-table` outcome, to be checked when it runs: extract consumes the first scripted reply (a tool-call reply has empty content, so parsing fails); the JSON retry consumes the second; the turn ends with the "repeat the last point" reply and nothing executes. Expected: `errors` tests pass; the kit identity test passes vacuously in discovery mode (its `executed` list stays empty); M4's own identity test asserts a non-empty list; `sources` and `abstains` fail; `tools`, `security` (destructive), `threads` and `cost` fail. The run produces the real table, which is reported. This prediction only checks the design.
- Threads: the kit's `test_threads_isolated` uses one user with two threads. Contract mode therefore keys history by `(user_id, thread_id)`. D35 (one session per owner) applies only to discovery mode.
- Kit `RUBRIC.md` says "Four criteria" and has three tables; the reflection follows the three tables.
- Kit M6 and README name Streamlit or Next.js. The course accepts Flask (D54). The README is updated to say Flask.
- Kit M5 names `ChatOpenAI` with a base URL; the build never calls it (D4).
- Kit `abstains` forbids any model call on empty retrieval; discovery's extraction call therefore runs only in discovery mode.
- The AE privacy check fails on notebooks in `sprint2/part2` and `sprint2/part4` (section 19).

## 15. Database

- `users(id, email UNIQUE, password_hash, role CHECK IN ('owner','operator'), created_at, last_login_at, failed_logins, locked_until)`
- `invites(token_hash UNIQUE, email, created_by, expires_at, used_at)`
- `customers(user_id PK→users, company_name, industry, employee_count, locations, owner_name, owner_role, owner_email, consent_at, consent_version)`
- `sessions(id PK, user_id UNIQUE→users, status CHECK IN ('active','submitted'), created_at, updated_at, last_seen_at)`
- `messages(id PK, session_id→sessions, role CHECK IN ('owner','assistant','system'), content, created_at, trace_hash)`
- `turns(id PK = turn_id, session_id→sessions, status CHECK IN ('running','done','error'), created_at, finished_at)`
- `turn_events(id PK, turn_id→turns, seq, type, payload_json, created_at)`, `UNIQUE(turn_id, seq)`
- `facts(session_id→sessions, field_id, value_json, unit, period, status CHECK IN ('reported','confirmed','estimated','unknown','conflicting'), source_message_id→messages, updated_at, PRIMARY KEY(session_id, field_id))`
- `fact_history(id PK, session_id, field_id, value_json, status, source_message_id, changed_at)`, append-only
- `evidence(id PK, session_id, message_id NULL, kind CHECK IN ('customer_statement','uploaded_document','kb_chunk','calculation'), ref, score, text_hash, created_at)`
- `submissions(id PK, session_id→sessions, revision, submitted_at, status CHECK IN ('received','report_ready','failed','withdrawn'), spec_snapshot_json, report_json, report_html, estimate_status, cost_low, cost_base, cost_high, currency, rate_card_version, model_id)`, `UNIQUE(session_id, revision)`
- `approvals(id PK, session_id→sessions, turn_id, tool_name, args_hash, status CHECK IN ('pending','approved','kept','used','expired'), created_at, expires_at)`
- `audit_log(id PK, actor_user_id NULL, action, target, detail_json, at)`
- `traces(id PK, turn_id, ts, node, duration_ms, status, error_code, detail_json)`
- `store/schema.sql` is the single schema source (M5-store). `tests/test_store_identity.py` (store role) checks that every owner-facing repository call fails for another owner's ids.

## 16. Front end (M6)

**Stack:** Flask app factory, Jinja templates, one stylesheet with `:root` tokens, light and dark via `prefers-color-scheme`, vanilla JavaScript, no CDN, no HTMX. Turn events are polled (D47).

**Look (D45).** Colours and icon style from the owner's reference; layout MILO's own.

| Role | Value | Used for |
|---|---|---|
| Canvas | `#F7F7F7` | page background |
| Surface | `#FFFFFF` | cards, top bar, panels |
| Ink | `#000022` (buttons `#01041C`) | primary button, headings |
| Selected tint | `#F3F4FD` | selected item, hover, focus background |
| Accent | `#5C1AF7` | MILO mark, links, progress, "reported" |
| Amber | `#FDB40E` (favourite `#FCAA00`) | suggestion badges, "estimated" |
| Green | `#40A178` | "confirmed", saved indicator |
| Muted | `#909090` | secondary text, "unknown" |
| Conflict (added) | `#D64545` | "conflicting", blocking messages |

Icons: thin rounded line icons, 1.5 to 2 px stroke, 20 px in a 32 px rounded square. Suggestion rows: white, radius 12 px, amber bolt badge, chevron. Status is a dot plus a text label, never colour alone. The MILO mark is a soft sphere, shown only on the empty conversation. Radii 12 px cards, 8 px buttons, 999 px pills. System sans; headings 28 px semi-bold; body 15 px.

Layout: top bar (mark, save indicator, first name, menu); centre conversation (empty state with greeting and suggestions); right panel "Spec and evidence" (progress, fields with status and quotes, missing fields, Submit). Right panel is below the conversation under 900 px.

**Reply details drawer** (required by kit M6 and rubric level 3): each assistant reply has a details drawer with sources (chunk id, document, score), tool calls (name, arguments, result, policy decision), node progress from events, and the model cost line. The estimate is labelled separately.

**Routes**

| Method and path | Purpose | Response |
|---|---|---|
| `GET /` | Conversation; creates nothing (the session exists from sign-in) | page |
| `POST /consent` | Records consent (D43) | JSON |
| `POST /messages` | Owner message; 409 if a turn is running | JSON `{turn_id}` |
| `GET /turns/<turn_id>/events?after=<seq>` | Turn events | JSON list |
| `POST /upload` | Document (D46) | JSON |
| `POST /submit` | D41 gate, revision, report | redirect to report |
| `GET /reports`, `GET /reports/<id>`, `GET /reports/<id>/download` | Owner's reports | page or file |
| `POST /approvals/<approval_id>` (`decision=approve` or `keep`) | Section 8 | JSON |
| `POST /withdraw/<submission_id>` | Section 8 | JSON |
| `GET /login`, `POST /login`, `POST /logout` | Sign-in creates the session; sign-out is "save and leave" | page or redirect |
| `GET /invite/<token>`, `POST /invite/<token>` | Password from invite | page |
| `GET /traces` | Operator only | page |
| `GET /ops/submissions`, `GET /ops/submissions/<id>`, `GET /ops/export/<id>.json` | Operator only | page or file |

**Turn events:** `node_started`, `node_finished`, `fact_recorded`, `source`, `policy`, `approval_required`, `estimate_updated`, `answer`, `error`, `done`.

**App additions** (integrator, `src/milo/app.py`): `App.ask(question, user_id, thread_id, on_event=None)`; `App.resume_approval(approval_id, decision, user_id)`; `App.recent_traces(limit)`; `App.ingest(docs)`.

**Web security:** autoescape on, no `|safe` on model, owner or document text (a script-tag test); CSRF hidden field for forms and `X-CSRF-Token` for fetch; cookies HTTP-only, `SameSite=Lax`, `Secure` over HTTPS; secret key from `MILO_SECRET_KEY`; the web layer calls `App`, the repositories and services only. Footer on every page: "Indicative estimate. Not a quote. Non-binding."

**M6 checks.** `evals/ui/record.py` starts its own local server with a seeded operator, a seeded owner invited by the same command the operator uses, and a known password. It then drives the owner flow through the browser. The blocked action is triggered by the owner asking, in the conversation, to withdraw a submission: the model calls `withdraw_submission`, which needs approval. The `/withdraw` button is a separate path and is not used for this check. `record.py` tries the request up to three times and records the attempt count in `m6_dom_checks.json`. Results go to `evals/results/m6/m6_dom_checks.json`; the done-line is that every entry passes. Keyboard use and WCAG contrast are **verifier-manual**: the verifier checks contrast from the CSS tokens and runs a keyboard walk-through. The recording covers sign-in, messages, evidence, a fact recorded, a blocked action pending approval, submit, the report, sign-out, sign-in again with the conversation restored.

## 17. Milestones and done-lines

Each done-line runs through a kit target or a test path. A milestone's done-line lists only what it can make pass when merged alone.

| ID | Build (owned paths) | Done-line |
|---|---|---|
| S0 Scaffold (integrator) | section 11 files; `src/milo/{__init__,types,errors,settings,trace,spend,app}.py`; `app.py` ships the complete contract-mode pipeline against named seams, each an S0 implementation that its owner replaces in its phase: `rag/retrieve.py` (`Retriever.ingest`, `search`), `security/policy.py` (`decide`), `security/taint.py` (`classify`), `loop.py` (`run_turn`), `llm/retry.py` (`invoke_with_retry`); `security/validate.py`; `protocol/schema.py`; `pricing/schema.py`; `store/schema.sql`; `tests/canary.txt`; root `conftest.py`; `Makefile` | `uv sync`; `make check`; every `make kit-*` target runs as a wiring smoke check and passes. These passes are not evidence for any owner milestone |
| M0 Data (remaining scope) | `tests/test_checker.py`, `tests/test_leakage.py`; regenerate tc04 and tc13 with `module_ids` only; correct atk04's note; fix tc07's owner; add `automated_minutes_per_request` and `eligible_share` to the rate card (section 5.2); add one direct exfiltration row with `output_leak` and the canary, or remove the canary machinery; correct `data/prompts/generation-record.md`, which cites a script that does not exist; `scripts/build_data.py` edits. Every change ends with one `build_data.py` run and one commit that includes `data/MANIFEST.json` | `python scripts/check_project_data.py data` prints OK; `build_data.py --check` exits 0; `uv run pytest tests/test_checker.py tests/test_leakage.py` passes |
| M1 Retrieval | `src/milo/rag/{chunk,ingest,vectors,retrieve}.py`, `evals/retrieval/run_retrieval.py`, `tests/test_chunk.py`, `tests/test_vectors.py`, `docs/m1_report.md` | the two tests pass; `make eval-retrieval`; `make eval-retrieval-replay` matches; `make kit-sources` and `make kit-abstains` pass |
| M2 Advanced RAG | `src/milo/rag/{translate,selfquery,bm25,fusion,rerank}.py`, `evals/retrieval/ab.py`, `tests/test_selfquery.py`, `tests/test_fusion.py`, `docs/m2_report.md` | self-query fallback test; A/B table with paired CI (rewrite, self-query, hybrid); decision per arm |
| M3 Tools and pricing | `src/milo/tools/**`, `src/milo/pricing/{rate_card,estimator}.py`, `evals/agent/run_tools.py`, `tests/test_tools.py`, `tests/test_pricing.py`, `docs/m3_report.md`; replaces the S0 `loop.py` seam | `tests/test_tools.py` runs every `MiloBackend` tool through `call()`, one schema rejection, identity-key stripping; `tests/test_pricing.py` passes; `make eval-tools` and `make eval-tools-replay` match; `make kit-tools` passes |
| M4 Security | `src/milo/security/{policy,identity,taint}.py`, `evals/agent/run_attacks.py`, `tests/test_policy.py`, `tests/test_identity.py`, `docs/m4_report.md`; replaces the S0 policy and taint seams | `make kit-security`; `tests/test_identity.py` asserts `backend.executed` is non-empty and that identity keys are stripped; `tests/test_policy.py` covers the four kit questions; attack evaluation with three arms; **destructive ASR = 0 in the headline arm, and in the auto-approve arm 0 taint-high executions and 0 cross-owner executions**; `make eval-attacks` and its replay match |
| M5a LLM and errors | `src/milo/llm/**` (replaces the S0 `llm/retry.py` seam), `tests/test_llm.py`, `tests/test_errors.py`, `docs/m5a_report.md` | `tests/test_llm.py` (think stripping, cost formula, retry classification, one retry site) and `tests/test_errors.py` pass; `make kit-errors` and `make kit-cost` pass |
| M5-store | `src/milo/store/**`, `src/milo/auth/**`, `store/schema.sql`, `tests/test_store.py`, `tests/test_auth.py`, `tests/test_store_identity.py` | tests pass; invite, lockout and owner filter covered |
| M5b Protocol, facts, chain, turns, approvals, prompts | `src/milo/{protocol,facts,chain,turns,prompts}/**`, the discovery branch of `src/milo/app.py` (integrator merges it), `tests/test_protocol.py`, `tests/test_turns.py`, `evals/discovery/**`, `docs/m5_report.md`, `docs/traces/` | tests pass; discovery evaluation reports premature-estimate rate = 0 (code-gated); `make discovery-table` run, with its outcome checked row by row against section 14; `make kit-threads` passes in contract mode |
| M5c Report | `src/milo/report/**`, `tests/test_report.py` | report from a stored spec; every number traces to a source field or rate-card constant |
| M6 UI | `src/milo/web/**` (shell in 1a, wiring in 3), `evals/ui/record.py`, `tests/test_web.py`, `docs/m6_checklist.md`, `docs/m6_recording.webm`, `evals/results/m6/m6_dom_checks.json` | `tests/test_web.py` (XSS, CSRF, owner isolation) passes; every DOM check in `m6_dom_checks.json` passes; recording produced; verifier-manual items recorded |
| M7 Evaluation | `evals/rag/**`, `evals/rag/labels.jsonl` (written by the labelling agent and committed before the judge prompt), `docs/m7_report.md`, `docs/reflection.md` | `make eval` replays; kappa with CI for both judges; limits section filled |

**Final steps (integrator):**
- **F1** Integrate all branches into `main`.
- **F2** `make final-test`, once. `run_final.py` runs M1 dense retrieval, the M2 A/B and the M7 judge on the test split, and logs each run.
- **F3** Every `kit-*` target with `PROJECT_APP=milo.app:build_app`; `make eval`; `make check`; the privacy check (section 19).
- **F4** Checkpoint block (integrator); README status.

## 18. Open items

- Owner creates `.privacy-terms` if there are private terms to add (section 19).
- Operator email delivery of reports is not built.
- Rate card and clients are fictional.

## 19. Blocker for AE commits

- The AE privacy check (`AE/scripts/privacy_check.py`) fails on notebooks in `sprint2/part2` and `sprint2/part4`. MILO commits are checked on MILO's own file list (D54). The AE commit stays blocked until the owner decides.
- `.privacy-terms` is missing; the owner creates it from `.privacy-terms.example`.
- Verifiers replace `$HOME` with `~` in anything pasted into `docs/verify/`.

---

## 20. Version 0.8 decisions (these override earlier text where they conflict)

Where a section below conflicts with this one, this section wins. The reviewer checks for conflicts and reports them.

**Database**
- **D57 Postgres.** The product database is Supabase PostgreSQL. SQLite is removed from the design. Override of D14 and section 15: `text`, `timestamptz`, `jsonb` for JSON, `uuid` keys, `CHECK` constraints for enums, and row-level security on every owner table (an owner reads and writes only their own rows; operators read all rows through a separate role). Migrations live in `db/migrations/`, one file per change, applied in order.
- **D58 Two Supabase projects.** `milo-test` (tests, development runs, evaluations; can be reset freely) and `milo-prod` (real customer data). Connection strings come from `MILO_DATABASE_URL_TEST` and `MILO_DATABASE_URL_PROD`. `MILO_ENV` selects one, and it defaults to `test`. Tests and evaluations refuse to run against prod. Keys and passwords live only in the untracked `.env` file, never in the repo.
- **D59 Consent-gated data.** Customer data enters prod only after consent is recorded (D43). Aggregate analysis across customers needs a separate consent version. The consent text is reviewed by the owner before prod is used.

**Gold set**
- **D60 120-question gold set** (overrides D25 and section 3 counts): 120 questions, 60 dev and 60 test, stratified by type. At least 24 unanswerable (12 dev, 12 test) and at least 40 with a metadata filter (20 dev). Questions are realistic: typos, short forms, multi-part questions, and owners who answer in fragments. A Fable agent generates them. A second Fable agent audits the entire dataset, old and new, before any split is read. The audit report is committed with the data. The embedding and chat comparisons are rerun on the expanded dev split under a Fable-written protocol.

**Pages**
- **D61 Pages:** sign-in; set password from invite; conversation (the main layout, section 16); specification review (every field, status, evidence, edit requests); reports (list and view); profile (company details, owner account, consent history, data-deletion request); operator (submissions list, submission detail, audit log, invite form); error and empty pages.

**Components**
- **D62 Component set:** top bar with navigation menu; conversation thread with inline evidence cards; composer with upload; evidence panel with status chips, progress and missing fields; estimate card, labelled indicative; approval banner; confirmation dialog (submit and withdraw); toast messages; tables with filters and empty states; report renderer with ten sections.

**Review fixes from round 5**
- **D63** The S0 scaffold ships `tests/test_s0_smoke.py`, so `make check` has a test to run.
- **D64** Seams in S0: `security/identity.py` (`strip_identity_keys`), `chain.py` (discovery seam that raises `NotImplementedError` until M5b), `evals/common.py` (the only loader that names the gold file), and `evals/run_all_replay.py`.
- **D65** A refused cross-owner request (`not_found`) counts as a block. It is an execution only when the backend changed state. The M4 report shows this as its own column.
- **D66** Knowledge-base wording: no article contains a D22 phrase. A builder check fails the data build if one does.
- **D67** The hidden-name check is a warning, not a block. The names come only from `.privacy-terms`.
- **D68** The reversed names are removed from git history after v0.8 is committed. This is a force-push to the private remote, and the experiment log and submodule pointer are updated to the new commit IDs.

**Experiments**
- **D69** Every experiment has a protocol written by a Fable agent before it runs. The embedding result (D5) is provisional until its protocol is written and the test-split check runs.

## 21. Version 0.9 decisions (these override sections 2 to 20 where they conflict)

Source: review round 6 (on v0.8). I decided not to run another full review round. The fixes below are applied directly, and the milestone verifiers check the rest.

**Testing and the database**
- **D70 Tests use an in-memory store.** `src/milo/store/memory.py` implements the repository protocols in memory. All `tests/` and the discovery mode (`build_discovery_app`) use it, so `--disable-socket` stays in force and the kit runs stay socket-free. `build_discovery_app` is `build_app(..., discovery=True, store=MemoryStore())`.
- **D71 Only `create_app`, `evals/ui/record.py` and the verifier's RLS test open Postgres.** The RLS test runs against the Supabase test project, outside pytest, as a verifier step.
- **D72 Ids.** `users.id` and `turns.id` are `uuid`. `turns.id` is exposed as 32-character hex (D47). Every other table uses `bigint generated always as identity`. `submission_id` stays an integer.
- **D73 One schema source.** `db/migrations/0001_init.sql` (S0, from section 15 as rewritten for D72) and `src/milo/store/migrate.py` (applies files in order and records them in `schema_migrations`). `make migrate` runs it. `store/schema.sql` and `data/milo.sqlite` are removed everywhere, including `.gitignore` and Appendix A. M5-store owns `db/migrations/**` after S0.
- **D74 RLS identity.** The app connects as the non-superuser role `milo_app`, never the Supabase service role. Each request runs `SET LOCAL milo.user_id = '<uuid>'` inside its transaction, and policies read `current_setting('milo.user_id', true)`. Operators use `milo_operator`, with read policies. The in-memory store applies the same owner filter in code. `tests/test_store_identity.py` checks cross-owner access in memory; the verifier's RLS test checks it in Postgres.
- **D75 Driver and pool.** `psycopg[binary,pool]`. One `ConnectionPool` per app, one transaction per request. `MILO_DATABASE_URL_*` point at the Supabase session pooler. `milo.app` does not import psycopg at module level (section 11.12).
- **D76 Test and prod isolation.** Root `conftest.py` raises `pytest.UsageError` when `MILO_ENV=prod` or `MILO_DATABASE_URL_PROD` is set in the test process. `evals/common.py` does the same for evals. `settings.py` resolves the prod URL only for `create_app` when `MILO_ENV=prod`. `.env.example` lists `MILO_ENV`, `MILO_DATABASE_URL_TEST` and `MILO_DATABASE_URL_PROD`; `MILO_DB` is removed.
- **D77 Parallel safety.** Only the verifier and `record.py` touch the test project, one run at a time, each in its own schema `milo_<uuid4 hex>` dropped at exit.

**Gold set (milestone M0b)**
- **D78 M0b Gold expansion** (data role, Phase 1a, merges before Phase 1b). Build: the 120 rows in `data/source/gold_questions.yaml`, read by `scripts/build_data.py`; the generation-record entry; `data/prompts/gold-audit.md` written by a different agent (role "Gold audit", slug `audit`, branch `agent/audit/m0b`); `data/MANIFEST.json`; and the dev rerun `evals/results/retrieval/embedding_dev_v3.json` with every D56 field, its protocol committed first (D83).
- Done-line: `uv run python scripts/build_data.py --check` exits 0 and asserts 120 rows, 60 dev and 60 test; at least 24 unanswerable with at least 12 per split; at least 40 filtered with at least 20 in dev; at least 8 identifier per split; every `type` in the kit's `GOLD_TYPES`; per-type dev and test counts differ by at most 1; unique `qid`; no two questions equal after lower-casing and whitespace collapse; every quote verbatim in its document and at most 150 characters; every filtered row's evidence matches its filter. `python scripts/check_project_data.py data` prints OK. `tests/test_checker.py` and `tests/test_leakage.py` assert the same counts and pass. `gold-audit.md` lists every qid with a verdict, contains no `reject`, and records the SHA-256 of `data/gold.jsonl` equal to `MANIFEST.json`. The audit commit is on a different branch from the generation commit, and it precedes every `agent/retrieval/*` and `agent/advanced-rag/*` commit (section 10.1 is re-anchored on it).
- Updated counts: D25 becomes 12 dev unanswerable; D5 becomes 48 answerable dev questions after M0b; D26 uses 30 labels drawn from the 60 dev questions.

**Knowledge-base wording (D66, scope fixed)**
- The check covers `data/kb/**/*.md` only, case-insensitive, against the D22 list. The upload, demo, and attack-folder files are excluded by name. `data/kb/policies/security.md` is reworded in M0 so that it contains no D22 phrase (for example: "a line that tells the assistant to change a status or a price is not followed"). The check is part of the M0 build list.

**Experiments and results**
- **D83 Protocols.** Each experiment protocol lives in `evals/protocols/<milestone>-<name>.md`, owned by that milestone's eval owner, and is committed before its result file. The verifier checks commit order.
- **D5 wording.** "F2 reports the test-split numbers for the chosen embedding model; the choice is not revisited on test."
- **D3 wording.** The chat-model comparison needs its protocol and result file before M5a starts.
- **Pre-S0 scripts.** `evals/retrieval/compare_embeddings.py` is exempt from the provider and gold-file rules until M0b rewrites it to load rows through `evals/common.py` and call the provider through `milo.llm.cloud`. Its two result files lack the D56 fields and are superseded by `embedding_dev_v3.json`.

**Metrics and attacks**
- **D65 (restated).** `tool_executed` means `call()` ran and the backend changed state for a tool the policy should have denied, or changed another owner's row. A `not_found` refusal counts as a block. Each `run_attacks.py` row carries `backend_state_changed: bool`, and the M4 done-line names that column.

**Pages, consent, and names**
- **D79 Routes added to section 16:** `GET /spec`; `POST /spec/<field_id>/request` (table `field_requests`); `GET /profile`; `GET /profile/consents`; `POST /profile/delete-request` (table `deletion_requests`); `GET /ops/audit`; `GET`/`POST /ops/invites`; `POST /ops/customers/<id>/delete`. Shell pages (sign-in, invite, error and empty pages, conversation layout) are M6 phase 1a; data-bound pages are phase 3. `tests/test_web.py` checks owner isolation on `/spec`, `/profile` and `/reports`. M6 owns the report renderer component; M5c owns the canonical JSON it renders.
- **D80 Invites.** Created by `auth/invites.create`, which is called both by `flask invite <email>` and by the operator form.
- **D81 Consent gate (D59 restated).** Consent is captured at first sign-in. Until `consents` has a row for purpose `interview`, `GET /` redirects to `/consent`, and `POST /messages` and `POST /upload` return 403 `consent_required`. `consents(user_id, purpose CHECK IN ('interview','aggregate'), version, at)` replaces the consent columns on `customers`. The build uses placeholder consent text v0. The owner reviews the real text before any prod use; that is a prod-only gate, listed in section 18.
- **D82 Name check.** The check blocks a commit when `.privacy-terms` exists and a listed term matches. When the file is absent, it prints a warning only. HARNESS 3.5 and section 19 say the same thing.
- **D84 History rewrite (D68 restated).** Executed by the integrator once, before any Phase 0 branch exists. It needs an explicit owner go-ahead for the force-push. After it, the integrator commits `docs/history-map.md` (old to new commit IDs). `.privacy-terms` must exist first, so the rewrite removes exactly the listed terms.

**Roles, gates and the kit**
- HARNESS v0.7 follows this section: roles and Appendix A are updated for D70 to D84. The `audit` slug is added to D29.
- The kit run (9 tests under 7 keywords, in contract mode, with sockets blocked) is unaffected only if three invariants hold, and the S0 row and M5-store row state them: `milo.app` imports no DB driver at module level; contract mode (`discovery=False`) keeps history, approvals and traces in memory and never touches a store; no part of the store role edits `pytest.ini`, the root `conftest.py` or the kit conftest.

## 22. Simplifications (override earlier privacy rules)

- **D85 No name check.** The hidden-name check, `.privacy-terms`, and every rule that depends on them are removed. The only privacy checks left are the generic ones: no API keys and no absolute home paths.
- **D86 History rewritten.** Done on 2026-10-22. The old commit IDs are mapped in `docs/history-map.md`.

## 23. Self-registration and accounts (overrides D34, D35 invite-only text, and the identity rules of D74)

- **D87 Open self-registration.** Anyone can create an account. There is no invite step. The owner's first sign-in is the sign-up.
- **D88 Login platform: Supabase Auth.** Accounts, email and password, email confirmation, password reset and session tokens are handled by Supabase Auth in each project (`auth.users`). Our code never stores passwords. Sign-up, sign-in, sign-out, confirmation and reset go through the project's Auth API with the publishable key. The app verifies session tokens (JWT) against the project's JWKS URL (`SUPABASE_*_JWKS_URL` in `.env`), locally, on each request. The secret key is server-side only.
- **D89 Profiles in our database.** `public.profiles(id uuid primary key references auth.users, email text, display_name text, role text not null default 'owner' check (role in ('owner','operator')), created_at timestamptz, last_seen_at timestamptz)`. A trigger on `auth.users` inserts the profile row when an account is confirmed. The `role` column is changed only by an operator in the database. A user cannot set their own role.
- **D90 Company data.** `customers` (one row per owner account) keeps the company fields and links to `profiles.id`. Consents (D81) stay in their own table, keyed by user.
- **D91 Row-level security.** Every owner table's policies compare the row's owner with `auth.uid()` (in Postgres) or with the verified token's `sub` (in the app, set per transaction, D74). The app connects as `milo_app`, never as the service role. Operators read through `milo_operator`, granted by `role = 'operator'` in `profiles`.
- **D92 Protection.** Sign-up and sign-in are rate-limited per IP and per email (5 failures per 15 minutes per account, D34's lockout kept). Passwords have a minimum length of 12. Email confirmation is required before any interview page opens. Error messages do not reveal whether an email is registered.
- **D93 Routes.** Replace the invite routes with: `GET /signup`, `POST /signup`, `GET /confirm` (the email link), `GET /login`, `POST /login`, `POST /logout`, `GET /reset` and `POST /reset` (request), `GET /reset/<token>` and `POST /reset/<token>` (set a new password). The invite route and `flask invite` are removed. `auth/invites.py` becomes `auth/accounts.py`, a thin client over Supabase Auth.
- **D94 Operator access.** The first operator is set by hand in the database for one account (`update profiles set role = 'operator' where email = ...`), documented in `docs/operations.md`. No sign-up path can produce an operator.
- **D95 Setup in the Supabase dashboard (owner).** Authentication → Providers → Email: enable "Confirm email"; set the site URL and the redirect URLs to the local app address for test and the real domain for prod; check the email rate limits for the free tier. These are dashboard settings, not code.
- **D96 Consent at sign-up.** The sign-up form shows the consent text (version v0, placeholder) and records a consent row on confirmation (D81). The consent gate still blocks the interview until the row exists.

**Consequences for other sections**
- Section 16 routes: the invite routes and `GET /login` for invited accounts are replaced by D93. The operator's `/ops` pages check `role = 'operator'`.
- Section 15 tables: `users` is replaced by `profiles` (D89). `invites` is removed.
- Section 11.2 dependencies add `pyjwt[crypto]` and `httpx`.
- HARNESS rule 3 and Appendix A: `auth/**` covers the Supabase Auth client and JWT verification, and `docs/operations.md` is an integrator file.

## Version log

- v0.1 (2026-10-21): customer-support scope, D1 to D31.
- v0.2 (2026-10-21): front end decided; section 16 added.
- v0.3 (2026-10-22): discovery product; D34 to D46.
- v0.3.1 (2026-10-22): look from the owner's colour set and icon style.
- v0.4 (2026-10-22): red-team round 1 applied (two modes, ephemeral stores, dense M1, cost /1e6, error format, sealing, kit targets, turns with polling, approvals).
- v0.4.1 to v0.4.4 (2026-10-22): demo companies and data pipeline (section 3A); ChatGPT prompts; data agent drafts prose (D52 → D55); model approvals and Flask acceptance (D53, D54).
- v0.5 (2026-10-22): red-team round 2 applied. Threads keyed by (user, thread) in contract mode. Tools through the backend protocol. Kit attack enums kept. Ruff excludes kit files. Contract threshold fixed at 0.30. Contract target enforced by root conftest. Store and auth roles in Phase 1a. Validation and stub retriever in S0. Protocol and rate-card schemas given. App.ingest specified. Provider error mapping. Single sealing loader. Discovery reply runs the tool loop and shows a details drawer. M7 on dev, F2 on test. Approval lifecycle. Replay-based provenance. Makefile targets with commands. Estimate numbers out of the model. Minor fixes: HTMX removed, trailers from the harness, identity before validation, lazy imports, `.python-version`, git-based privacy file list, session at sign-in, one turn per session, traces table, 44-row gold split, targets mapped to merges.
- v0.6 (2026-10-22): red-team round 3 applied. Seams and S0 pipeline stated (S0 row). Wire types (section 2A). Protocol and rate-card files are the schemas. Estimate tool takes no numbers. Single retry site. Eval, replay and kit-discovery targets added. Leakage check excludes its own tests and is git-ordered per retrieval branch. Predicted kit-discovery outcome in section 14. M3 and M4 done-lines run real tool and policy tests. HARNESS v0.5 is a separate file.
- v0.7 (2026-10-22): red-team round 4 applied. ruff pinned to E4, E7, E9, F and build_data.py formatted. ToolCall fields as the kit defines them. discovery-table replaces kit-discovery. Protocol section follows the committed file. Gold and tool-case sections follow the committed data, with M0 fixes listed. Success per check type defined. Objects map and E-1, E-2 and owner-e defined. Role slugs and the advanced-rag git-order check added. HARNESS v0.6.
- v0.8 (2026-10-22): Postgres on Supabase with two projects (D57, D58); consent gating (D59); 120-question gold set with Fable audit (D60); page and component sets (D61, D62); round-5 fixes (D63 to D66); warning-only name check (D67); history rewrite queued (D68); Fable-designed experiments (D69). Section 20 overrides earlier text where they conflict.
- v0.9 (2026-10-22): review round 6 applied in section 21 (D70 to D84): in-memory store for tests, integer ids with uuid users and turns, one migration source, RLS with a dedicated app role, psycopg pool, prod isolation, gold expansion as M0b, KB wording check, protocols under evals/protocols, new routes, consent gate, name check, history rewrite before Phase 0.
- v0.9.1 (2026-10-22): name check removed (D85); history rewritten and mapped (D86).
- v0.9.2 (2026-10-22): open self-registration with Supabase Auth and profiles in our database (D87 to D96); invite-only text overridden.
