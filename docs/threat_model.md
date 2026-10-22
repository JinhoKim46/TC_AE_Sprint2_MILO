# MILO threat model (SR lane)

Status: v0.2, 2026-10-22, checked by the integrator (D124); every gap in section 6 is closed or assigned by SPEC section 28 (D127 to D146). Written against `SPEC.md` v0.9.6 and `HARNESS.md` v0.9. Sections 20 to 27 of the spec win over earlier text, so the system modelled here is Supabase Postgres with `profiles`, JWT verified locally, RLS through `milo_app`, many sessions per account (D97), and the two-stage input gate (D110 to D118). The repo is pre-S0 at the time of writing: there is no `src/`, `tests/`, `db/`, `evals/security/` or `evals/protocols/` yet, so every "existing check" below is `none`, with the test that SPEC 17 plans in brackets. Facts the spec does not state are marked `unverified`.

Scope: the whole system (web, store, auth, gate, tools, prompts, uploads, logs, reports), as D122 asks. Owner of this file: SR. Attack cases `A-nn` go to `evals/security/` and are committed before the defenses they test (D123).

## 1. Assets

| Asset | Where it lives | Why it matters |
|---|---|---|
| Owner account data | `auth.users` (Supabase Auth), `public.profiles`, `customers` | Login identity, role, contact fields (pii) |
| Company facts and evidence | `facts`, `fact_history`, `evidence`, `messages` | The product output; the report is built from them |
| Consent records | `consents(user_id, purpose, version, at)` | Legal basis for storing any data (D59, D81) |
| Specification revisions and submissions | `submissions` (immutable, `UNIQUE(session_id, revision)`) | Audit record the operator reviews |
| Operator access | `profiles.role = 'operator'`, role `milo_operator`, `/ops/*`, `/traces` | Reads every owner's rows; deletes customers |
| OpenRouter and Supabase keys | git-ignored `.env`: `OPENROUTER_API_KEY`, `MILO_SECRET_KEY`, Supabase secret key, DB URLs, JWKS URL | Spend, session forgery, full DB access |
| Prod database | Supabase project `milo-prod` | Real customer data (D58) |
| Knowledge base and index | `data/kb/**`, `data/index/` | Retrieved text goes into the model context |
| Reports | `submissions.report_json`, `report_html`, `/reports/<id>/download`, `/ops/export/<id>.json` | Contains facts, quotes, estimates |

## 2. Actors

| Actor | Trust | Notes |
|---|---|---|
| Anonymous visitor | none | Reaches `/signup`, `/login`, `/reset`, `/confirm`, error pages |
| Self-registered owner | authenticated, untrusted | Anyone can register (D87). May be hostile to other owners, to the operator, or to the budget |
| Operator | trusted, high value | One account set by hand (D94). No MFA stated (unverified) |
| Model output | untrusted | Chat replies, tool calls, extracted facts, `evidence_text` |
| Uploaded text | untrusted (D46) | PDF or text, 5 MB, becomes evidence `uploaded_document` |
| Knowledge-base documents | untrusted in the prompt (D22), trusted in git | Fictional, wording-checked (D66). Compromise only through the repo |
| Third-party text pasted by an owner | untrusted | Emails, vendor quotes, procedures; carries injections the owner did not write |
| Jev decision model (`typesafe/jev-1.14-20261014`) | external judge, not an authority | Returns a probability; code routes on thresholds (D115); fails open (D117) |

## 3. Trust boundaries and data flow

```text
[browser] --TB1--> [Flask web layer] --TB5--> [gate: stage 1 rules -> stage 2 Jev] --> [chain]
                        |                                                    |
                        |--TB2--> [Supabase Postgres as milo_app, RLS]        |--TB3--> [OpenRouter: chat, embeddings, Jev]
                        |                                                    |--TB4--> [MiloBackend tools: search_knowledge, get_session_spec,
                        |                                                               estimate_cost, record_fact, withdraw_submission]
                        '--TB6--> [logs/trace.jsonl, logs/spend.jsonl, traces table, turn_events, reports, /ops/export]
```

1. **TB1 Browser to Flask.** Cookies HTTP-only, `SameSite=Lax`, `Secure` over HTTPS; CSRF hidden field and `X-CSRF-Token`; autoescape on, no `|safe` on model, owner or document text (section 16). Identity comes only from the verified JWT (D88); the web layer calls `App`, repositories and services only.
2. **TB2 Flask to Supabase.** JWT verified locally against the JWKS URL on each request; the app connects as the non-superuser role `milo_app`, never the service role (D74, D91); each request runs `SET LOCAL milo.user_id = '<sub>'` inside its transaction and RLS policies read `current_setting('milo.user_id', true)`; operators read through `milo_operator`. The in-memory store repeats the owner filter in code (D70).
3. **TB3 App to OpenRouter.** Only `src/milo/llm/cloud.py` holds the URL and client (HARNESS rule 1). Chat (extract, reply, tool follow-up), embeddings and the Jev gate call cross it. Everything that comes back is untrusted. Cost is read from the usage field and written by `spend.py`; a run below the 1.00 USD reserve is refused (D18).
4. **TB4 Tool calls to `MiloBackend`.** The model proposes `ToolCall(name, args)`; identity keys `user_id`, `session_id`, `owner_id`, `thread_id` are stripped before schema validation (D21); `additionalProperties: false`; `user_id` comes from the session; policy by `ToolSpec.risk` and taint (section 8); destructive calls need an owner approval bound to (session, turn, tool, args hash), 10-minute expiry.
5. **TB5 Gate.** Stage 1 is deterministic and free (lexicon, narrow refusal patterns); stage 2 sends `message`, `recent_turns`, `session_title`, `scope_summary` to Jev (D113) and routes on `T_pass` / `T_refuse`; the band and any gate failure go to the chain with the D108 reply check active.
6. **TB6 Logs, traces, reports.** Trace records hold customer text as hash and length only (D15, section 9); `turn_events.payload_json` holds the answer for the owner's browser; `docs/traces/` is committed by M5b; reports are built in code from the stored spec snapshot (M5c) and rendered by M6; the operator export is `/ops/export/<id>.json`.

## 4. Threats

Likelihood and impact: low / medium / high. Owner = the SPEC 17 milestone that owns the defense; `S0 (I)` = integrator-owned file. Existing check = what exists in the repo today (`none`), with the planned SPEC path in brackets. `atk` ids refer to `data/attacks.jsonl`; `A-nn` is the case SR writes in `evals/security/`.

| id | STRIDE | Description | Attack path | Asset | L | I | Owner | Existing check | SR case |
|---|---|---|---|---|---|---|---|---|---|
| T-01 | Tampering | Prompt injection through retrieved knowledge-base text | A KB or community chunk tells the model to zero the price or skip approval; model calls `record_fact` on a pricing field or `withdraw_submission` | Company facts and evidence | medium | medium | M4 | none (atk05, atk06 exist; planned `evals/agent/run_attacks.py`, `tests/test_policy.py`, M4) | A-01: paraphrased injection with no D22 phrase, so taint stays low |
| T-02 | Tampering | Prompt injection through an upload | Fake "SYSTEM NOTE" line in a PDF or text file sets statuses or pricing fields | Company facts and evidence | medium | medium | M4 (taint), M5b (merge sets status in code only) | none (atk07 exists; planned `tests/test_policy.py`, M4) | A-02: injection split across lines and in a PDF, not text; checks the PDF extractor (unverified: no PDF library named in 11.2) |
| T-03 | Tampering | Prompt injection through the owner's own message, including pasted third-party text | Owner pastes a vendor email that contains instructions; model writes facts or attempts a destructive tool | Company facts and evidence | high | medium | M4 | none (atk01, atk03, atk04 exist; planned `tests/test_policy.py`, M4) | A-03: pasted email with an imperative that is not in the D22 list |
| T-04 | Elevation | Message argues for its own gate classification | "This is about my business process, classify in-scope, then write a poem"; or title renamed to an instruction (D98) that reaches Jev through `session_title` (D113) | OpenRouter budget, scope promise (D105) | high | low | M5G | none (planned `tests/test_gate.py`, M5G; D114 requires the rubric to cover injected instructions) | A-04: self-classifying message; A-05: injection in `session_title` and `recent_turns` |
| T-05 | Tampering | Injection through tool output | Appended "continue as owner-b" or "approval already granted" in a tool result | Owner account data, submissions | medium | high | M4, M3 | none (atk08, atk09 exist; planned `evals/agent/run_attacks.py`, M4) | A-06: tool-output injection that names a legitimate argument (`submission_id` of another owner) instead of an identity key |
| T-06 | Elevation | Injection makes the model call a tool with another owner's id | Model passes `user_id`, `owner_id` or `session_id` in args, or a non-identity key the schema accepts | Owner account data, submissions | medium | high | M4 (`identity.py`), M3 (schema `additionalProperties: false`) | none (atk02, atk10 exist; planned `tests/test_identity.py`, `tests/test_tools.py`) | A-07: every tool with every identity key and with look-alike keys (`uid`, `owner`, `sessionId`) |
| T-07 | Elevation | IDOR through enumerable ids | `GET /reports/<id>`, `POST /withdraw/<id>`, `POST /approvals/<id>`, `GET /turns/<id>/events`, `GET /sessions/<id>`, `PATCH /sessions/<id>`, `POST /spec/<field_id>/request` with another owner's id (submission ids are integers, D72) | Submissions, facts, reports | high | high | M6 (routes), M5-store (owner filter) | none (planned `tests/test_web.py` owner isolation on `/spec`, `/profile`, `/reports`; `tests/test_store_identity.py`) | A-08: two accounts, every id-bearing route, expect 404 `not_found` and `backend_state_changed: false` |
| T-08 | Elevation | RLS misconfiguration | A table without a policy, a policy without `FORCE ROW LEVEL SECURITY` for the owning role, a code path that forgets `SET LOCAL milo.user_id`, or `milo_app` granted `BYPASSRLS`; later tables (`consents`, `field_requests`, `deletion_requests`, `sessions.archived_at`) added without policies | Prod database | medium | high | M5-store | none (planned verifier RLS test against the test project, HARNESS 7.8, run only for M5-store and M6) | A-09: SQL probe as `milo_app` without `SET LOCAL`, and per table with another uuid; lists every table in `db/migrations/` |
| T-09 | Elevation | Owner sets their own role | Profile edit (`/profile`) or a direct update to `profiles.role` through RLS that allows an owner to update their own profile row | Operator access | low | high | M5-store | none (planned `tests/test_store_identity.py`; D89 says the column changes only by an operator) | A-10: update `profiles.role` as the owner through every write path; expect no change |
| T-10 | Elevation | Consent-gate bypass | Send to `POST /messages` or `POST /upload` with no `interview` consent row; or write data through routes D81 does not name (`POST /sessions`, `POST /spec/<field_id>/request`, `POST /submit`) | Consent records | medium | medium | M6 (routes), M5-store (`consents`) | none (planned `tests/test_web.py`, M6; HARNESS rule 13) | A-11: every state-changing route before consent, expect 403 `consent_required` or redirect, no row written |
| T-11 | Elevation | Approval bypass | Resolve another owner's approval id; approve twice (race on `status`); approve after `expires_at`; replay an approval with different args (hash binding); model claims approval was granted | Submissions | medium | high | M5b (lifecycle), M3 (backend) | none (atk04, atk09 exist; planned `tests/test_turns.py`, M5b; `make kit-security`) | A-12: cross-owner resolve, double approve in parallel, expired approve, args changed after approval |
| T-12 | Tampering | XSS through model or owner text | `<script>` in a message, an `evidence_text`, a tool argument shown in the drawer, a session title, a company name in the report; markdown rendering of replies (unverified: no renderer named) | Owner account data (session), operator access | medium | high | M6 | none (planned `tests/test_web.py` script-tag test, M6) | A-13: payloads in every rendered field, light and dark pages, drawer and report |
| T-13 | Elevation | Stored XSS rendered to the operator | Owner text in `spec_snapshot_json` or `report_html` shown on `/ops/submissions/<id>` or `/ops/audit`; `report_html` is stored pre-rendered, so marking it safe in Jinja reproduces owner text (unverified how M6 renders it) | Operator access | medium | high | M6 (renderer), M5c (canonical JSON) | none (planned `tests/test_web.py`, M6; D40) | A-14: payload submitted as a revision, operator pages checked for unescaped output |
| T-14 | Spoofing | CSRF | Cross-site form or fetch to `POST /submit`, `/withdraw/<id>`, `/approvals/<id>`, `/consent`, `/sessions/<id>/archive`, `/profile/delete-request`, `/ops/customers/<id>/delete` | Submissions, consent records | medium | high | M6 | none (planned `tests/test_web.py` CSRF test, M6) | A-15: every POST and PATCH without the token and with a token from another session |
| T-15 | Spoofing | Session fixation and cookie flags | Flask session not rotated at sign-in; missing `HttpOnly`, `SameSite`, `Secure`; `MILO_SECRET_KEY` empty or a dev default so the cookie is forgeable; JWT `aud`, `iss` and algorithm not pinned (unverified); token still valid after sign-out or password reset (unverified, D88 local verification) | Owner account data | medium | high | M5-store (`auth/**`), M6 (cookie config), S0 (I) (`settings.py` start-up guard) | none (planned `tests/test_auth.py`, M5-store; `tests/test_web.py`, M6) | A-16: cookie flags, session id before and after login, `alg=none` and wrong-`aud` tokens, reuse after logout |
| T-16 | Spoofing | Login brute force | Password guessing on `POST /login`; lockout only per account so a distributed attack rotates emails | Owner account data | medium | medium | M5-store (`auth/ratelimit.py`), owner item 1 (Supabase settings) | none (planned `tests/test_auth.py` lockout, M5-store) | A-17: 6 failures per 15 min per email and per IP; in-process limiter across two workers (unverified: limiter storage) |
| T-17 | Denial | Self-registration abuse | Mass sign-up exhausts confirmation emails on the free tier (D95), disposable emails make many "companies", no CAPTCHA stated | Owner account data, service availability | high | medium | M5-store, owner item 1 | none (planned `tests/test_auth.py` sign-up, M5-store) | A-18: 50 sign-ups from one IP in a minute, expect rate-limit response |
| T-18 | Information disclosure | Email enumeration | Different error text, status or timing on `/signup`, `/login`, `/reset` for a registered email | Owner account data | high | low | M5-store | none (D92 says messages do not reveal; planned `tests/test_auth.py`) | A-19: compare body, status and timing for known and unknown emails |
| T-19 | Information disclosure | Secrets or personal data in logs, traces, reports or error pages | Tool arguments (`evidence_text` is owner text) written to `detail_json`; Jev state logged; `docs/traces/` committed with non-demo text; stack trace or Werkzeug debugger on a 500 (unverified: debug mode not stated); keys in `logs/spend.jsonl`; verifier pastes | Keys, owner account data | medium | high | M5b (`docs/traces/`, trace nodes), S0 (I) (`trace.py`, `spend.py`, `errors.py`), M6 (error pages) | none (planned `tests/test_errors.py`, M5a; privacy check for keys, D54) | A-20: canary in a message and in an upload, grep every log, trace, event payload, report and error page; force a provider exception |
| T-20 | Tampering | Production database touched by tests or evals | `MILO_ENV=prod` in a developer shell; `make migrate` or `record.py` reading the prod URL; env var names differ between HARNESS rule 3 and D58/D76 so a guard checks the wrong name | Prod database | low | high | S0 (I) (`conftest.py`, `evals/common.py`, `settings.py`), M5-store (`migrate.py`) | none (planned root `conftest.py` `UsageError`, `evals/common.py` guard, D76) | A-21: run pytest, `make migrate`, `record.py` and `run_attacks.py` with the prod variables set, expect refusal with zero connections |
| T-21 | Elevation | Scope-gate bypass by encoding or language | Zero-width characters, homoglyphs, base64, leetspeak, or a non-English request for a poem or code; stage 1 lexicon is English (unverified); a reply that names a module to pass the D108 reply check | OpenRouter budget | high | low | M5G, M4 (stage 1, `security/scope.py`) | none (planned `tests/test_gate.py`, M5G; `data/scope/` set) | A-22: out-of-scope set encoded five ways and in three languages, expect refusal rate held |
| T-22 | Denial | Cost exhaustion | 4000-char messages with 4 model calls per turn and 3 attempts each; many sessions per account (D97) so one running turn per session is no account limit; uploads of 5 MB fed to the extract call (unverified: no token cap on upload text); repeated stage-2 Jev calls; one account drains the balance to the 1.00 USD reserve and stops the service for everyone | OpenRouter budget, availability | high | medium | S0 (I) (`spend.py`), M5b (turn deadline), M5G (gate cost), M6 (per-route limits) | none (planned `tests/test_llm.py` retry bounds, M5a; D109 zero-cost test, M5G) | A-23: 20 parallel sessions, max-length messages and one 5 MB upload, measure spend and concurrency |
| T-23 | Tampering | Supply-chain risk of dependencies | Unpinned or typosquatted packages, the pytorch-cpu index, Playwright browser download, chromadb telemetry, `yaml.load` instead of `safe_load` | Keys, prod database | low | high | S0 (I) (`pyproject.toml`, `uv.lock`) | none (lockfile planned; no audit tool named) | A-24: static check of `uv.lock` hashes and index sources, grep for `yaml.load(` and `pickle` |
| T-24 | Tampering | Path traversal or SSRF through uploads | Filename `../../.env` if the original file is stored to disk (unverified: storage location not stated); URL-bearing PDF fields fetched by an extractor; JWKS URL taken from anything but `.env`; PDF decompression bomb under the 5 MB cap | Keys, availability | low | high | M6 (`POST /upload`), M5b (extraction) | none (planned `tests/test_web.py`, M6) | A-25: traversal names, a 5 MB PDF that expands past 500 MB, wrong MIME with a PDF magic number |
| T-25 | Information disclosure | Report export leaks another owner's data | `estimate_cost` or `get_session_spec` read facts across sessions of the same account or across accounts; upload text embedded into the shared index and retrieved for another owner (unverified: D46 does not say whether uploads are indexed); `/reports/<id>/download` without the owner filter | Reports, company facts and evidence | medium | high | M5c (report), M3 (tools), M1 (index), M6 (download route) | none (planned `tests/test_report.py`, `tests/test_web.py`) | A-26: canary fact in account A, build report and search in account B |
| T-26 | Repudiation | Audit log tampered or deleted | `milo_app` has update or delete on `audit_log` or `fact_history`; operator deletion without the audit row | Submissions, consent records | low | medium | M5-store | none (planned `tests/test_store.py`) | A-27: attempt update and delete on `audit_log` as `milo_app` and `milo_operator` |
| T-27 | Tampering | Fabricated evidence | Model returns an `evidence_text` that is not in the owner's message or document; merge stores it; the report quotes it | Company facts and evidence | high | medium | M5b (merge) | none (no verbatim check named in section 5) | A-28: scripted extract reply with an invented quote, expect rejection or status `estimated` |
| T-28 | Elevation | Operator account compromise | Password reuse or phishing on the single operator account; no MFA stated (unverified) | Operator access, prod database | low | high | owner item 3, M5-store | none | A-29: review only; check that `/ops/*` re-reads `profiles.role` per request, not from the cookie |

## 5. What is out of scope

- Physical security and the owner's workstation.
- OpenRouter's and Supabase's own infrastructure, key storage and provider models' training-time behaviour.
- Volumetric denial of service at the network or infrastructure level (TLS termination, DDoS). Application-level cost exhaustion (T-22) is in scope.
- Attacks that need the git-ignored `.env` already in hand.
- The course kit files (`contract/`, `scripts/check_project_data.py`), which are read-only copies.
- The fictional demo companies' "real" counterparts; no real data exists in the build (HARNESS rule 5).

## 6. Gaps the spec does not yet close

- G-01 HARNESS rule 3 names two Supabase session-pooler variables for the connection strings; SPEC D58 and D76 name `MILO_DATABASE_URL_TEST` and `MILO_DATABASE_URL_PROD`. The prod guard (T-20) must check one set; which is unstated.
- G-02 HARNESS v0.9 says it matches SPEC v0.9.3; SPEC is v0.9.6. Rules 11 and 12 and Appendix A do not yet reflect D110 to D125 (stage-2 cost, SR lane).
- G-03 Stage-1 gate ownership is split: section 24 and Appendix A put it in `src/milo/security/scope.py` (M4); section 17 and D120 put stage 1 in `src/milo/gate/**` (M5G).
- G-04 D74 says policies read `current_setting('milo.user_id', true)`; D91 says they compare with `auth.uid()`. As `milo_app` over psycopg only the former can be set; the predicate must be one of them.
- G-05 D91 gives `milo_operator` read policies only, but `POST /ops/customers/<id>/delete` and the audit row need writes; no write policy or role is named.
- G-06 No per-account cost, concurrency or request-rate limit exists; only the global 1.00 USD reserve in `spend.py`. One hostile account can stop the service for every owner (T-22).
- G-07 No token cap on uploaded text before the extract call; 5 MB of text exceeds any context window and costs are unbounded.
- G-08 `evidence_text` is not checked verbatim against the source message or document (T-27).
- G-09 D81 names only `POST /messages` and `POST /upload` for the consent gate; `POST /sessions`, `POST /spec/<field_id>/request` and `POST /submit` are not covered in text.
- G-10 JWT verification details are unstated: accepted algorithms, `aud` and `iss` checks, clock skew, token lifetime, and what happens to a token after sign-out or password reset.
- G-11 Flask session rotation at sign-in, a start-up refusal when `MILO_SECRET_KEY` is empty, and `FLASK_DEBUG` off in production are not in the D121 production guard.
- G-12 No CSP, `X-Frame-Options` or HSTS header is specified. With no CDN a strict CSP is cheap and would contain T-12 and T-13.
- G-13 Where uploaded files are stored, whether the original is kept, and which PDF library parses them are unstated (T-24). Section 11.2 lists no PDF dependency.
- G-14 Whether upload text is embedded into a vector index, and if so whether the collection is per session, is unstated (T-25).
- G-15 Whether model replies are rendered as markdown is unstated; a markdown renderer without a sanitizer reopens T-12.
- G-16 The RLS test runs in Postgres only for M5-store and M6 (HARNESS 7.8). Tables added by later milestones (`field_requests`, `deletion_requests`, `consents`) have no named Postgres check.
- G-17 Trace `detail_json` content is unspecified; the hash-and-length rule (D15) names customer text but not tool arguments, `evidence_text`, Jev state or event payloads.
- G-18 Rate-limiter storage for D92 is unstated; an in-process limiter does not hold across workers or restarts.
- G-19 No dependency audit step (lock-file hash check or vulnerability scan) is in `make check`.
- G-20 No MFA for the operator account, and `/ops/*` role lookup per request versus cached in the cookie is unstated.
- G-21 D117 fail-open on a gate failure is by design and accepted by the spec; it is listed here so the pen test measures it, not as a gap to close.

## 7. Test-environment rules for SR (D125)

1. Test environment only. HARNESS rule 3 applies: the Supabase test project, the in-memory store and scripted fakes; evals refuse the prod URL; no run touches `milo-prod`.
2. No real owner data and no production keys. Attack cases use the fictional owners (`owner-a`, `owner-b`, `owner-e`) and the canary in `tests/canary.txt`.
3. A critical finding blocks the merge. A high finding is fixed, or the owner accepts it in writing. An agent never accepts a risk.
4. SR has one fifth of the OpenRouter budget remaining at its start, with its own cap in the spend ledger (`logs/spend.jsonl`). Every run that spends has a protocol committed first (D83) and a D56 result file.
