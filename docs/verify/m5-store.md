# Verify M5-store (store, auth, migrations, RLS)

Branch verified: `verify/m5-store` at `2a89058` (= `agent/m5-store` `22ff99c` merged with `main`). Fresh verifier
agent; the builder's notes were not read. No model call was made. Postgres run against the TEST project only
(HARNESS 7.8, SPEC D154); every value from `.env` is replaced by `<redacted>` and `$HOME` by `~`.

## Verdict: FAIL

The offline done-line passes in full and the row-level security itself holds in Postgres, but the Postgres run
as SPEC D153 and HARNESS 7.8 write it does not work on the test project: migration 0001 leaves the administrative
role unable to `SET ROLE milo_app`, so `tests/test_store_identity.py --postgres` fails at its first repository
call (finding F1, high). The RLS evidence below was obtained only after a verifier-applied workaround. Two SR
cases are unmet (A-09c force RLS, A-17 per-IP limit) and are listed for the fix cycle.

## Part 1. Offline checklist

### 7.2 Done-line

```text
$ uv run pytest tests/test_store.py tests/test_auth.py tests/test_store_identity.py
============================= 135 passed in 0.51s ==============================

$ make check            (ruff check, ruff format --check, mypy src/milo, uv lock --check, pytest, data checks, size check)
============================= 345 passed in 1.80s ==============================
OK: 0 problem(s)
OK: 0 problem(s)
{ ! git ls-files -z | xargs -0 -r du -b | awk '$1>2097152' | grep -v m6_recording; }

$ uv run pytest tests/
============================= 345 passed in 1.73s ==============================

$ make kit-sources    1 passed, 8 deselected
$ make kit-abstains   1 passed, 8 deselected
$ make kit-tools      1 passed, 8 deselected
$ make kit-security   2 passed, 7 deselected
$ make kit-errors     2 passed, 7 deselected
$ make kit-threads    1 passed, 8 deselected
$ make kit-cost       1 passed, 8 deselected
```

`import milo.app` with `socket.socket` replaced by a raising stub: imported; `sys.modules` holds no `psycopg`,
`psycopg_pool`, `httpx`, `jwt` or `milo.auth.*` module; `milo.store` modules loaded: `milo.store`,
`milo.store.common`, `milo.store.memory` only. PASS (SPEC 11.12, D75).

### 7.3 Hard rules

| Rule | Check | Result |
|---|---|---|
| 3 variable names | `grep -rn` for the five pooler variable names of D153, excluding `.venv` and `.git` | `src/milo/settings.py:18-22`, `.env.example:20-31`, `evals/security/cases.jsonl:42` (A-21); the rule text itself in `HARNESS.md:48`, `SPEC.md:624,658`. No other file. PASS |
| 3 Supabase domain | `grep -rn` for the Supabase domain suffix named in HARNESS rule 3 | only `HARNESS.md:48` (the rule). PASS |
| 3 service-role key | `grep -rni "service.role\|service_role"` in `src`, `tests`, `db`, `evals`, the two M5-store docs | prose and negative tests only (`db.py:3,42`, `accounts.py:3`, `test_store.py:490,665`, `test_auth.py:761`, `operations.md:21,44`). No key. PASS |
| 3 roles | `src/milo/store/db.py:24,37-44,84-93`: `ALLOWED_ROLES = ("milo_app", "milo_operator")`; `check_role` refuses any other URL role; the admin string is accepted only with `admin=True` (migrate, reset) or `set_role=` (RLS test) | PASS |
| 4 secrets in diff | `git diff main...agent/m5-store` grepped for the OpenRouter key prefix, the AWS key prefix, the GitHub token prefix, the private-key header, a database URL carrying a password, and an absolute home path | 0 hits. PASS |
| 1 `openrouter.ai` | grep under `src`, `evals`, `tests` | 1 hit outside `llm/cloud.py`: `evals/retrieval/compare_embeddings.py:61`. Pre-existing on `main`, an M0b path, not in this diff (F5, for the integrator) |
| 2 offline tests | `pytest.ini` `--disable-socket`; `conftest.py:24,36` | PASS (345 passed with sockets blocked) |
| 12 no operator by sign-up | `0001_init.sql:390-391` (milo_app: SELECT, UPDATE(last_seen_at) only on profiles); `test_store.py:91` | PASS |

### 7.4 Owned paths

`git diff --name-only main...agent/m5-store` (38 paths): `db/migrations/0001_init.sql`, `docs/m5-store_report.md`,
`docs/operations.md`, `docs/requests/m5-store.md`, `src/milo/auth/*` (7), `src/milo/store/*` (16),
`tests/test_auth.py`, `tests/test_store.py`, `tests/test_store_identity.py`. All inside the M5-store rows of
Appendix A (D156). Nothing outside. PASS

### 7.5 to 7.7

`docs/m5-store_report.md` reports no eval numbers; no `evals/results/m5-store/` file is required and none exists.
No dev eval to replay. Not applicable.

### Code read against the spec

| Topic | Spec | Where | Result |
|---|---|---|---|
| JWT algorithms | D136 | `auth/jwt.py:22,145-146`: header `alg` must be RS256 or ES256 before any key lookup; `:153` must equal the JWK's advertised algorithm and `use=sig`; `:160` `algorithms=[alg]` | PASS (`alg=none`, HS256 rejected: `test_auth.py:123,135,156`) |
| aud, iss, exp, sub, skew | D136 | `jwt.py:23-24,157-165`: `audience="authenticated"`, `issuer` from `settings.auth_config()`, `leeway=60`, `require=[exp, sub, aud, iss]` | PASS (`test_auth.py:189,196`) |
| role claim unused | D144 | `jwt.py:176-189` reads `sub`, `email`, `exp`, `session_id` only; `users.py:49-51` re-reads `profiles.role` | PASS |
| Lockout per account | D92 | `auth/ratelimit.py:27` `LOGIN_FAIL_ACCOUNT = 5 / 900 s`; `login.py:83-90` check before Supabase, record on failure, same path for unknown emails | PASS (`test_auth.py:613,636`) |
| Lockout per IP | D92, A-17 | `ratelimit.py:28` `LOGIN_FAIL_IP = 20 / 900 s` | UNMET against the brief (5 per IP) and A-17 (F3) |
| Same reply for unknown emails | D92 | `accounts.py:119-125,183-195` map every auth error but `email_not_confirmed` to `InvalidCredentials`; sign-up and reset return nothing (`:159-170,216-225`) | PASS; note `EmailNotConfirmed` is a distinct error (`accounts.py:117`), which Supabase returns only after the password matched (informational) |
| Rate limits in a table | D142 | `0001_init.sql:245-251` `rate_limits(scope, key, window_start, count)`; `store/ratelimits.py:20-32` upsert; keys hashed (`ratelimit.py:38-39`) | PASS (`test_auth.py:711,718`) |
| One running turn, stale cutoff | D99, D131 | `sessions.py:151-181`: advisory lock per account, stale `running` turns older than `STALE_TURN_S = 300` (`common.py:49`) closed as `error`, then `select 1 from turns where status='running'` under the owner's RLS identity, i.e. per account | PASS (`test_store.py:141`) |
| Approvals bound to args hash, expiry | D48 | `approvals.py:18,50-54` `args_hash(args)`, `expires_at = now() + 600 s`; `:60-74` pending past expiry becomes `expired` on read; `:76-101` transitions under `for update`, `APPROVAL_TRANSITIONS` (`common.py:30-36`) | PASS (`test_store.py:258`) |
| Trace and audit details refuse owner text | D141 | `common.py:53-69,122-138` forbidden keys at any depth, strings over 80 chars refused; used by `traces.py:31` and `audit.py:31` | PASS (`test_store.py:287`) |
| Cross-owner calls | D74, D129 | every Postgres repository call opens `db.transaction(who)` (`db.py:122-138`: `set local role`, then `set_config('milo.user_id', ..., true)`) and uses `require_row` or `returning`; RLS violations (42501) and FK (23503) map to `NotFound` (`db.py:27,59-68`) | PASS (in memory 52/52; in Postgres 52/52, see Part 2) |
| Operator writes | D130 | `0001_init.sql:384-388`: `revoke all`, then SELECT on all, DELETE on customers, INSERT on audit_log; `audit.py:54-69` delete and audit row in one transaction | PASS (confirmed from `information_schema` in Part 2) |
| migrate refuses prod | D155 | `migrate.py:86-94`: `MILO_ENV=prod` needs `MILO_MIGRATE_PROD=1`; `MILO_ENV=prod uv run python -m milo.store.migrate` -> `RuntimeError: refusing to migrate production: set MILO_MIGRATE_PROD=1 by hand (SPEC D155)` | PASS |
| reset refuses prod | D154 | `reset.py:27-32`: no override; `MILO_ENV=prod uv run python -m milo.store.reset` -> `RuntimeError: db-reset refuses production, with no override` | PASS |
| Prod guard in tests | D76, D127 | `MILO_ENV=prod uv run pytest tests/test_store.py` -> `ERROR: tests must not run with production settings: MILO_ENV=prod`; the prod administrative pooler variable set to the string `sentinel-unreachable` for `uv run pytest ...` -> `ERROR: ... prod database variable`; `evals/common.py:29,50` call `assert_not_prod()` | PASS |
| Migration hygiene | D155 | `0001_init.sql`: `create or replace function` (5), `drop trigger if exists` before both triggers, no `search_path`; second `make migrate` printed `applied: nothing to do` | PASS, except the grant guard (F1) |

## Part 2. Postgres run (test project only)

Environment: `MILO_ENV` unset; only the test administrative pooler variable (D153) exported, read from the
git-ignored `.env` of the main checkout. That `.env` also holds a non-empty prod administrative pooler variable,
which was never exported: a count of environment variables with the prod prefix printed `0` before every command. Server: PostgreSQL 17.11; `current_user` = `postgres`, `rolsuper = false`, `rolcreaterole = true`,
`createrole_self_grant` empty. Output passed through a filter that replaces the host, user, project ref,
password and the full string with `<redacted>`.

### (a) `uv run python -m milo.store.migrate`

```text
applied: 0001_init
```

Created: roles `milo_app`, `milo_operator` (login, nosuperuser, nobypassrls, nocreaterole, nocreatedb); 18 tables
in `public` (`schema_migrations`, `profiles`, `customers`, `consents`, `sessions`, `messages`, `turns`,
`turn_events`, `facts`, `fact_history`, `evidence`, `submissions`, `approvals`, `audit_log`, `traces`,
`field_requests`, `deletion_requests`, `rate_limits`); 37 policies; 5 functions; trigger
`on_auth_user_confirmed` on `auth.users`. A rerun printed `applied: nothing to do`.

### (b) `uv run python tests/test_store_identity.py --postgres`

The script's only option is `--postgres` (`docs/operations.md:65` documents `--owner-a`, `--owner-b`,
`--schema`, which it does not accept: F4).

First run, as documented: FAIL.

```text
psycopg.errors.InsufficientPrivilege: permission denied to set role "milo_app"
  (src/milo/store/db.py:130, "set local role milo_app")
... tests/test_store_identity.py:369 in main -> seed -> users.py:92 -> milo.errors.NotFound: not_found
exit=1
```

Cause (queried): after migrate, `pg_auth_members` held one row per role, `(milo_app, postgres,
admin_option=true, inherit_option=false, set_option=false)`. On PostgreSQL 16+ a CREATEROLE non-superuser that
creates a role is granted that role with ADMIN OPTION only. The guard at `db/migrations/0001_init.sql:35-42`
checks `exists (select 1 from pg_auth_members ...)`, finds that row and skips `grant milo_app to current_user`,
so the administrative role has no SET option and D153's "the verifier's RLS test ... runs `SET ROLE milo_app`"
cannot hold. `make migrate` reports success.

Verifier workaround (not part of the branch): `grant milo_app, milo_operator to current_user` once on the
administrative connection, which added a second membership row with `set_option=true`. Then:

```text
$ uv run python tests/test_store_identity.py --postgres
PASS sessions.get ... PASS field_requests.add_own      (52 PASS lines, one per call)
52 passed, 0 failed
exit=0
```

All 36 `NOT_FOUND` calls (another owner's session, turn, message, submission, approval ids, and the three mixed
cases) raised `not_found`; the 8 `EMPTY` and 8 `OWN` calls showed nothing of owner A and changed none of A's
rows. The two accounts were created in `auth.users` (the trigger made their profiles) and deleted at the end.

### (b, continued) Verifier SQL probes (A-09a, A-09c, A-10b, A-27, D130)

The committed `--postgres` suite exercises repository calls only; the table-level probes of the SR cases were run
by the verifier with the same administrative connection, each probe in its own rolled-back transaction after
`SET LOCAL ROLE`, with owner A seeded through the repositories. 27 of 28 checks passed.

A-09c (per table from `db/migrations`, `pg_class` and `pg_policies`):

```text
table              rls   force  policies
approvals          True  False  2
audit_log          True  False  2
consents           True  False  2
customers          True  False  3
deletion_requests  True  False  2
evidence           True  False  2
fact_history       True  False  2
facts              True  False  2
field_requests     True  False  2
messages           True  False  2
profiles           True  False  2
rate_limits        True  False  2
schema_migrations  True  False  1
sessions           True  False  2
submissions        True  False  2
traces             True  False  2
turn_events        True  False  2
turns              True  False  2
PASS every migrated table exists; RLS enabled on every table; a policy on every table (schema_migrations excepted)
FAIL RLS forced on every table  [relforcerowsecurity = false on all 18]
PASS milo_app and milo_operator: rolsuper=false, rolbypassrls=false, rolcreaterole=false
PASS auth.uid() occurrences in db/migrations = 0; current_setting('milo.user_id', true) occurrences >= 1
PASS trigger on_auth_user_confirmed exists on auth.users
```

A-09a (`SET LOCAL ROLE milo_app`, no `milo.user_id`): `select count(*)` returned 0 rows for 16 tables and
SQLSTATE 42501 (no SELECT grant) for `schema_migrations` and `audit_log`; `insert into sessions (user_id, title,
status) values (<owner-a>, 'x', 'active')` -> 42501; row count of `sessions` unchanged. PASS.

A-09b (`milo.user_id` = owner B, owner A's rows in every owner table): select = 0 in all 15 owner tables;
update rowcount 0 (customers, sessions, turns, facts, submissions, approvals) or 42501 where milo_app has no
UPDATE grant (profiles, consents, field_requests, deletion_requests, messages, turn_events, fact_history,
evidence, traces); delete = 42501 everywhere; A's row counts unchanged. PASS.

A-10b: owner `update profiles set role='operator'` -> 42501; owner `insert into profiles` -> 42501; operator
`update profiles set role` -> 42501; `role_after = owner`; milo_app's UPDATE privilege on `profiles` is the column
`last_seen_at` only (`information_schema.column_privileges`). PASS.

A-27: milo_app update/delete on `audit_log` -> 42501/42501, on `fact_history` -> 42501/42501; milo_operator
update/delete on `audit_log` -> 42501/42501; milo_operator insert on `audit_log` allowed (rolled back); row counts
unchanged. PASS.

D130 grant inventory (`information_schema.role_table_grants`): milo_operator non-SELECT grants are exactly
`[(audit_log, INSERT), (customers, DELETE)]`; milo_app has no grant on `audit_log` or `schema_migrations`, no
UPDATE or DELETE on `fact_history`, DELETE only on `rate_limits`; milo_operator reads every owner's sessions and
may delete a customer (rolled back). PASS.

### (c) `uv run python -m milo.store.reset`

```text
db-reset: ran 26 statements; the test project holds no MILO objects
exit=0
```

Confirmation query (administrative connection): MILO tables left in `public`: `[]`; all `public` tables: `[]`;
MILO functions left: `[]`; roles `milo_app`, `milo_operator` left: `[]`; trigger `on_auth_user_confirmed` on
`auth.users`: 0; policies in `public`: 0; test accounts in `auth.users`: 0. PASS.

## Findings

| # | Where | What | Spec | Severity |
|---|---|---|---|---|
| F1 | `db/migrations/0001_init.sql:31-45` | The guarded `grant %I to current_user` is skipped whenever any `pg_auth_members` row exists; on PostgreSQL 16+ the creating CREATEROLE role already holds an ADMIN-only membership (`set_option=false`), so after a clean migrate the administrative role cannot `SET ROLE milo_app` and `tests/test_store_identity.py --postgres` fails at `db.py:130`. Fix: grant unconditionally (a repeated grant is a notice, not an error) or test `set_option` in the guard. Note `tests/test_store.py:761-764` asserts the guarded form and the commented literal at `0001_init.sql:46`, so the fix changes that test too. | D153, D155, HARNESS 7.8 | high; fails the milestone |
| F2 | `db/migrations/0001_init.sql:286-359` | No table has `force row level security`; `relforcerowsecurity = false` on all 18. SR case A-09c expects force. Practical exposure is low because the app never connects as the table owner (`db.py:37-44`), but note that `handle_confirmed_user`, `owns_session` and `owns_turn` are `security definer` and `migrate.py` inserts into `schema_migrations` as the owner, so adding FORCE needs owner-role policies (or those functions fail). | A-09c, T-08, D140 | A-09c says critical if it fails; assessed medium here, open SR finding for the owner (D125) |
| F3 | `src/milo/auth/ratelimit.py:28` | `LOGIN_FAIL_IP = 20` per 15 min. The brief and A-17 expect the per-IP limit to trigger when the email is rotated after six failures from one address; at 20 it does not. D92's numbered limit is per account only. | D92, A-17, T-16 | medium |
| F4 | `docs/operations.md:60-69`; `src/milo/store/db.py:79,83,95-96,108` | Section 5 documents `--owner-a`, `--owner-b`, `--schema`, a throwaway schema and D77, and "the test project's `milo_app` URL"; the script accepts only `--postgres`, uses the administrative string and `SET ROLE` (D153, D154). `Database(schema=...)` is dead code citing the withdrawn D77. | D153, D154 | low |
| F5 | `evals/retrieval/compare_embeddings.py:61` | `openrouter.ai` outside `llm/cloud.py` (hard rule 1). Pre-existing on `main`, M0b-owned, not in this diff. For the integrator. | HARNESS 3.1 | low (out of scope) |
| F6 | `tests/test_store_identity.py:336-388` | D140 says the RLS test "enumerates tables from the migrations, so a new table without a policy fails it". The `--postgres` path runs the repository suite only; it does not read `pg_class`/`pg_policies`, nor probe the no-`milo.user_id` path (A-09a), `profiles.role` (A-10b) or `audit_log` (A-27). The verifier ran those by hand this time. | D140, HARNESS 7.8 | medium |
| Info | `src/milo/auth/accounts.py:117,192`, `auth/errors.py:41` | `email_not_confirmed` is a distinct reply; Supabase emits it only after the password matched, so it does not enumerate registered emails for a guesser. | D92 | informational |

## SR review

| Case | Threat | Runner | Evidence | Result |
|---|---|---|---|---|
| A-09a | T-08 | postgres-test | milo_app without `SET LOCAL`: 0 rows in 16 tables, 42501 on `schema_migrations` and `audit_log`; insert into `sessions` 42501; no row added | met |
| A-09b | T-08 | postgres-test | owner B vs A's rows in 15 owner tables: select 0, update 0 or 42501, delete 42501, A unchanged; repository suite 52/52 | met (after F1 workaround) |
| A-09c | T-08 | postgres-test | RLS enabled and a policy on every table; roles not super/bypassrls/createrole; `auth.uid()` 0, `current_setting(...)` present; `relforcerowsecurity` false on all 18 | unmet (force), F2 |
| A-10b | T-09 | postgres-test | owner update 42501, owner insert 42501, operator update 42501, `role_after = owner`; column grant `last_seen_at` only | met |
| A-27 | T-26 | postgres-test | milo_app and milo_operator update/delete on `audit_log` 42501; milo_app on `fact_history` 42501; operator insert allowed; counts unchanged | met |
| A-16 | T-15 | offline | token checks: `alg=none`, HS256 key confusion, wrong `aud`, wrong `iss`, expired beyond 60 s, bad signature rejected (`test_auth.py:123,135,156,189,196,210`); `logout` revokes server-side (`accounts.py:208-214`). Cookie flags and session rotation are M6 (`login.py:4-5`) | met for the M5-store part |
| A-17 | T-16 | offline | per-account 5/15 min (`test_auth.py:613`), shared counters across two service instances (`:718`), 429 with window (`errors.py:26-35`); per-IP limit is 20 (`test_auth.py:661`), so email rotation after 6 failures is not stopped | partly unmet, F3 |
| A-18 | T-17 | offline | `SIGNUP_IP = 10 / h` (`ratelimit.py:29`); 11th sign-up from one address raises `rate_limited` and is not forwarded (`test_auth.py:674-685`, `login.py:57-61` limits before the client call) | met |
| A-19 | T-18 | offline | login: one `invalid_credentials` for wrong password and unknown email with the same user message (`test_auth.py:474-493`); sign-up and reset return the same for registered and unknown emails (`:496`, `accounts.py:216-225`); route-level body/status is M6 | met for the M5-store part |
| A-21 | T-20 | offline | pytest refuses `MILO_ENV=prod` and a set prod variable (`UsageError` tails above); `evals/common.py:29,50` guard; migrate refuses prod without `MILO_MIGRATE_PROD=1`; reset refuses prod outright; zero connections attempted | met |

Note: the verifier brief labelled the no-`milo.user_id` probe "A-09b" and the RLS-enabled-and-forced probe
"A-09a"; in `evals/security/cases.jsonl` those are A-09a and A-09c. This table uses the ids of `cases.jsonl`.

## For the fix cycle

1. F1: make the grant in `0001_init.sql` unconditional or guard on `set_option`, update the form assertion in
   `tests/test_store.py:761-764`, and rerun HARNESS 7.8 from a clean test project (`make migrate`,
   `tests/test_store_identity.py --postgres`, `make db-reset`).
2. F2 and F6: decide FORCE RLS with the owner (D125) and move the per-table RLS-flag and A-09a/A-10b/A-27 probes
   into the `--postgres` path so D140's "enumerates tables from the migrations" holds in the committed test.
3. F3: set the per-IP login limit as the owner decides and record it as a `Dnn` line if it stays above 5.
4. F4: rewrite `docs/operations.md` section 5 for D153/D154 and drop the D77 `schema` option from `db.py`.
