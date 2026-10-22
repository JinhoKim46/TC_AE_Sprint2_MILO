# Verify: M6 phase 1a (web shell)

Verdict: **PASS** (verifier checklist, HARNESS section 7) and **SR review: no critical or high finding** (SPEC D123 step 3, D124).

- Branch `agent/m6-shell` at `626bc99` (two commits on top of merge-base `c74b195`; `main` is `83d6549`, so the three-dot range `main...agent/m6-shell` is used throughout).
- Fresh worktree, branch `verify/m6-shell`, `uv sync` first. No model call, no network, no database; `.env` not read.
- The owner's notes (`docs/m6_checklist.md` narrative) were not read beyond the item list.

## 1. Done-line (SPEC 17, M6 row; phase-1a slice)

`uv run pytest tests/test_web_shell.py`

```text
tests/test_web_shell.py ..............................                   [100%]
============================== 30 passed in 6.32s ==============================
```

`make check`

```text
tests/test_errors.py ............                                        [ 11%]
tests/test_llm.py .................................                      [ 43%]
tests/test_s0_smoke.py .............................                     [ 71%]
tests/test_web_shell.py ..............................                   [100%]
============================= 104 passed in 9.89s ==============================
{ test ! -f data/gold.jsonl || uv run python scripts/build_data.py --check; }
OK: 0 problem(s)
{ test ! -f data/gold.jsonl || uv run python scripts/check_project_data.py data; }
OK: 0 problem(s)
{ ! git ls-files -z | xargs -0 -r du -b | awk '$1>2097152' | grep -v m6_recording; }
exit=0
```

(ruff check, ruff format --check, mypy strict, `uv lock --check` all passed before the pytest block.)

`uv run pytest tests/`

```text
tests/test_s0_smoke.py .............................                     [ 71%]
tests/test_web_shell.py ..............................                   [100%]
============================= 104 passed in 5.72s ==============================
```

Seven kit targets (`PROJECT_APP=milo.app:build_app MILO_ADVANCED_RAG=0 MILO_LIVE= uv run pytest contract -k <k> -rA`):

| Target | Result |
|---|---|
| `make kit-sources` | 1 passed, 8 deselected, exit 0 |
| `make kit-abstains` | 1 passed, 8 deselected, exit 0 |
| `make kit-tools` | 1 passed, 8 deselected, exit 0 |
| `make kit-security` | 2 passed, 7 deselected, exit 0 |
| `make kit-errors` | 2 passed, 7 deselected, exit 0 |
| `make kit-threads` | 1 passed, 8 deselected, exit 0 |
| `make kit-cost` | 1 passed, 8 deselected, exit 0 |

No `skip` or `xfail` in any run (hard rule 9).

## 2. Hard rules (HARNESS section 3)

| Rule | Check | Result |
|---|---|---|
| 1 Models only through OpenRouter | `grep -rn openrouter.ai src/ evals/ tests/` | 2 hits, both allowed: `src/milo/llm/cloud.py:18`, `evals/retrieval/compare_embeddings.py:61` (pre-S0, SPEC exemption) |
| 2 Offline tests | `pytest.ini` keeps `--disable-socket`; `conftest.py` refuses a run without it; `import milo.app, milo.web, milo.web.dev` with `socket.socket` and `create_connection` patched to raise | import ok, no socket |
| 4 No secrets, no machine paths | `git diff main...agent/m6-shell` grepped for the five patterns of the checklist (the OpenRouter key prefix, the AWS key prefix, the GitHub token prefix, the private-key header, the home-directory prefix) | 0 matches |
| 4 (note) | `src/milo/web/dev.py:22` `DEV_PASSWORD = "dev-only-password"` | fictional in-memory fixture for the dev factory, which refuses `MILO_ENV=prod` (`dev.py:284`); the generic privacy check passes; not treated as a secret (see finding F3) |
| 12 Self-registration only | `POST /signup` with `role=operator` creates an owner (SR row A-10a) | pass |
| 13 Consent before data | every write route 403 `consent_required` before the interview row (SR rows A-11) | pass |
| Web security (SPEC 16, D138) | `|safe`, `| safe`, `Markup(` in `src/milo/web/`: 0; inline `<script` without `src=`, `<style`, ` style=`, ` on*=` in templates: 0 | pass |

## 3. Owned paths (HARNESS 7.4, Appendix A M6 phase-1a row)

`git diff --name-only main...agent/m6-shell` lists 30 paths (`wc -l`): `docs/m6_checklist.md`, `src/milo/web/__init__.py`, `dev.py`, `ports.py`, `routes.py`, `static/css/app.css`, `static/js/app.js`, 22 files under `src/milo/web/templates/` (`grep -c templates/`), and `tests/test_web_shell.py`. Every path lies inside the rows `src/milo/web/**` (shell and static files), `tests/test_web_shell.py`, `docs/m6_checklist.md`. Paths outside: none.

## 4. Items 5 to 9

- 7.5 and 7.6 (result files with D56 fields, replay): not applicable, the shell reports no eval number and has no `evals/results/m6/` file.
- 7.7 (dense retrieval rerun): not applicable.
- 7.8 (RLS test per migration, D140): not applicable, the diff adds no file under `db/migrations/`.
- 7.9 (gold count): not applicable.

## 5. SR review (SPEC D123 step 3, D124)

Runner: `evals/results/security/m6-shell-review.py` (a throwaway verifier script, committed with this verdict together with its output `m6-shell-review.json` at the coordinator's request; the JSON carries `git_commit` = the `verify/m6-shell` head at run time, whose `src/milo/web/**` is byte-identical to `626bc99` (the verify branch adds only `docs/verify/` and this runner), `model`/`seed`/`temperature` = null (no model call), `command`, `timestamp`, `inputs` = SHA-256 of `xss.txt`, `traversal_names.txt`, `cases.jsonl`, `routes.py`, and `rows`). It runs Flask test clients against `create_flask_app(FakeApp(), DevStore(), Settings(env="test", secret_key=32 bytes), debug=False)` over `https://localhost`, a second app with `env="prod"` for HSTS, and `create_dev_app()` for the debug-mode cookie contrast; sockets are blocked at import. Routes are enumerated from `url_map` (22 GET rules, 18 write rules, as the A-13b evidence line prints), never from a hand list. Every count below is copied from the script's evidence strings in the JSON. The store is deep-copied before and after every walk for `backend_state_changed`.

Result: 39 checks, 37 pass, 2 fail, both low. No critical or high failure.

| Case | Check | Result | Evidence |
|---|---|---|---|
| A-15 (T-14) | every POST/PATCH/DELETE without a token, with another session's token (form field and `X-CSRF-Token` header) and anonymous -> 403 `csrf_failed` | pass | 18 write rules x 4 variants, 0 failures; store unchanged |
| A-15 | CSP `form-action 'self'`; `app.js` sends `X-CSRF-Token` from the meta tag; every template form carries `csrf()` | pass | `routes.py:36-39`, `app.js` `api()`, `_macros.html` |
| A-13b (D138) | the four headers with the exact values on every response: every route x anonymous/owner/operator, 404, 405, 500, static file, 401, 403, 501 | pass | 125 responses, 0 mismatches; HSTS absent outside prod |
| A-13b (T-19) | forced 500 renders `error.html` without a traceback | pass | `/boom` -> 500, heading "Something went wrong", no "Traceback" or exception text |
| A-13b | prod adds `Strict-Transport-Security: max-age=31536000` | pass | `Settings(env="prod")` GET /login |
| A-13b / A-14 | no inline `<script>`, `<style>`, `style=`, `on*=`; no `|safe` or `Markup(` | pass | 0 matches across 22 templates and `web/*.py` |
| A-16a (T-15) | cookie flags `HttpOnly`, `SameSite=Lax`, `Secure` with debug off | pass | `session=<value>; Secure; HttpOnly; Path=/; SameSite=Lax` |
| A-16a / D137 | session regenerated at sign-in: cookie value and CSRF token both change | pass | both differ |
| A-16a / D137 | sign-out: cookie cleared, old CSRF token rejected (403), new token differs, `/` redirects | pass | `logout` -> 302, `Set-Cookie session=; Expires=Thu, 01 Jan 1970…` |
| A-16a | dev factory (debug) drops only `Secure` | pass | `HttpOnly; Path=/; SameSite=Lax` |
| D137 | prod without `MILO_SECRET_KEY` refuses | pass | `RuntimeError: MILO_SECRET_KEY is required in production` |
| D137 | web factory refuses prod + debug | **fail, low** | `create_flask_app(..., Settings(env="prod", secret_key=…), debug=True)` constructs; the guard `settings.production_problems` (`settings.py:74`) has no caller in `src/milo` (finding F2, integrator) |
| A-13a (T-12, D139) | each of the 13 `xss.txt` payloads placed at once in session title, user message, model reply, source/tool-call fields, evidence value and quote, missing field, estimate, first name, report title/section, submission customer/title, audit actor/target, consent version, trace dict, and the reflected search query `/sessions?q=<payload>`; 15 pages read per payload (owner and operator) plus the 404 page for `/<payload>` | pass | 13 payloads x 195 page reads; raw payload absent, escaped form (`&lt;` or `&#34;`) asserted present wherever the marker appears, 0 live `<script`/handler/`<iframe`/`<svg`/`<style`/`javascript:` matches, `{{SRX7731*7}}` and `${SRX7731}` literal, markdown link not an anchor, `line one\nline two` newline kept |
| A-14 (T-13) | operator pages `/ops/submissions`, `/ops/submissions/<id>`, `/ops/audit`, `/traces` escape owner text | pass | included in the walk above |
| A-13a | error text and consent text are fixed strings | n/a | `routes.py` constants; `upload_filename` not rendered in 1a (501) |
| A-08 / A-13b | anonymous: owner pages 302 -> `/login`, owner JSON routes 401 `unauthorized` (with a minted token; without one the CSRF check answers 403 first, by design `routes.py:131-137`), `/ops/*` and `/traces` 404 | pass | 30 non-auth rules walked anonymously; anonymous `POST /logout` -> 401 (harmless) |
| A-08f (T-07) | owner (non-operator) gets 404 on every `/ops/*` and `/traces` route including `POST /ops/customers/<id>/delete`; store unchanged | pass | 6 operator rules; store unchanged |
| A-08 | JSON 404 error code is `not_found` | **fail, low** | fetch GET `/ops/submissions` as owner -> `{"error": "page_not_found"}` (finding F1) |
| A-08a / A-08b | owner-b reads owner-a's `/sessions/<id>`, `/reports/r1`, `/reports/r1/download` -> 404, no owner-a text in the body | pass | 404, 404, 404; store unchanged |
| A-08b | owner-b `PATCH`/`archive`/`restore` on owner-a's session | pass (1a) | 501 `not_built`, title and status unchanged; the owner check must come with the phase-3 wiring (D102; note N1) |
| A-29 / D144 | operator role re-read per request | pass | 200 before demotion in the store, 404 after, same cookie |
| A-11 (T-10, D81, D135) | `GET /` without the interview row -> 302 `/consent` | pass | |
| A-11 | every owner write route before consent -> 403 `consent_required`, 1 KB body or file, no row written | pass | 11 write rules; store unchanged |
| A-11 | `/consent`, `/login`, `/signup`, `/reset`, `/reset/<token>`, `/confirm` reachable before consent; `POST /logout` -> 302 | pass | 200/200/200/200/200/400, 302 |
| A-11 | `POST /consent agree=yes` -> 302, row recorded, `GET /` 200; without `agree` -> 400 `invalid_input`, no row | pass | |
| A-19 (T-18, D92) | sign-up, sign-in, reset: identical status and body (tokens stripped) for a known and an unknown email | pass | 200/200, 401/401, 200/200; bodies equal |
| D92 | password of 11 characters refused on sign-up (400) and reset (400); 12 accepted | pass | `MIN_PASSWORD_LEN = 12`, `routes.py:33` |
| D92 | sign-in refused before email confirmation | pass | 401 |
| A-10a / rule 12 | `POST /signup role=operator` creates an owner | pass | `role=owner` |
| A-25 (T-24) | 13 traversal names (NUL byte decoded) as the upload filename | pass | every upload 501 `not_built`; `git status --porcelain --ignored`, cwd and parent-dir listings, the temp-dir entries whose names carry a traversal token, and the `.env` presence identical before and after (changed keys: none; no `.env` in the worktree) |
| A-25 | no file write in the shell by construction | pass | `routes.py` has no `.save(`, `open(`, `Path(` or `os.path`; `MAX_CONTENT_LENGTH` 10 MiB (note N2) |
| D139 (G-15) | reply with markdown, a URL and `<b>` renders as escaped literal text with newlines kept; no `<strong>`, no anchor; `.plain-text { white-space: pre-wrap }`; `app.js` uses `textContent` only, no `innerHTML` | pass | |
| D139 | no `markdown`, `nl2br` or `urlize` filter in templates | pass | |

### Findings

| # | Where | What | Spec | Severity |
|---|---|---|---|---|
| F1 | `src/milo/web/routes.py:466-468` (`error_page`) | the JSON error code is derived from the heading, so a 404 answers `page_not_found` and 403/405 answer `not_allowed`; the SR cases A-08a to A-08f expect `not_found` (SPEC section 8 and D65 name `not_found`) | A-08, D65 | low; fix in phase 3 (one constant per status) |
| F2 | `src/milo/settings.py:74` `production_problems` has no caller; `src/milo/web/routes.py:501-522` `create_flask_app` accepts `env="prod"` with `debug=True` | D137 "production refuses to start when FLASK_DEBUG is set or the Werkzeug debugger is enabled" is implemented as a function but not wired into any factory; `create_app` is integrator-owned (`app.py`, (I)), so this is a request to the integrator, not an M6 failure. The web factory does refuse prod without `MILO_SECRET_KEY` | D137 | low (integrator) |
| F3 | `src/milo/web/dev.py:22` | `DEV_PASSWORD = "dev-only-password"` literal; hard rule 4 says "passwords in code". Assessed as not a secret: it unlocks only the fictional in-memory accounts of the dev factory, which raises on `MILO_ENV=prod` (`dev.py:284-285`); the generic privacy check (`privacy_check.py`, run without `.privacy-terms`) passes on 1888 files | HARNESS 3.4 | informational, no action |
| F4 | `src/milo/web/static/css/app.css:13,87,95` | light-mode danger text `#d64545` on surface `#ffffff` is 4.38:1, under the 4.5:1 the checklist asks for ("Danger text on surface"); used for `.btn-danger` and `.msg-error` text. Every other token pair checked is at or above 4.5:1 in both schemes (ink 19 to 20, muted 5.8 to 7.7, accent 5.9 to 7.1, button text 18 to 20; dark danger 7.06). Green and amber are dots only, with a text label (SPEC 16) | SPEC 16, checklist contrast items | low (accessibility, verifier-manual item; phase 3) |

Notes for phase 3 (not findings): N1 `PATCH /sessions/<id>`, `/archive`, `/restore`, `/approvals`, `/withdraw`, `/spec/<id>/request` answer 501 before any owner check; A-08b to A-08e stay open until the wiring adds the D102 owner filter (`tests/test_web.py`). N2 `MAX_CONTENT_LENGTH` is 10 MiB while D46/D132 cap an upload at 5 MB; the per-file cap belongs to the phase-3 upload handler. N3 The CSRF check precedes identity (`routes.py:131-137`), so an anonymous write without a token sees 403 `csrf_failed` rather than 401; acceptable. N4 The keyboard walk-through and the remaining checklist boxes are verifier-manual items of the phase-3 recording and stay open.

## 6. Verdict

PASS. Done-line green (30, 104, 104 tests; seven kit targets), hard rules clean, every changed path inside the M6 phase-1a rows, and the SR review finds no critical or high issue: CSRF, D138 headers, cookie flags and session regeneration, escaping of every rendered field, anonymous and operator access, the consent gate, email-enumeration parity, the 12-character minimum, traversal names and plain-text replies all hold. Two low findings (F1 naming, F2 integrator wiring) and two informational notes (F3, F4) are recorded for phase 3 and the integrator.
