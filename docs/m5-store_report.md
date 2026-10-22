# M5-store report

Status: built, offline tests pass, not yet run against Postgres (that is the verifier's step, HARNESS 7 item 8).

## What exists

- `src/milo/store/`: `db.py` (pool, per-transaction `milo.user_id`), repositories `users.py` (profiles, customers,
  consents), `sessions.py` (sessions, turns), `messages.py`, `facts.py` (facts, history, evidence),
  `submissions.py`, `approvals.py`, `traces.py`, `ratelimits.py`, `requests.py`, `audit.py` (audit, operator),
  `common.py` (shared validation), and `memory.py` (a mirror of each, over the S0 `MemoryStore`).
  `milo.store.memory_repositories()` and `pg_repositories(db)` return the same set of attributes.
- `src/milo/auth/`: `accounts.py` (Supabase Auth over httpx), `jwt.py` (JWKS verification), `passwords.py`,
  `login.py` (service that ties them together), `ratelimit.py`, `errors.py`.
- `tests/test_store.py`, `tests/test_auth.py`, `tests/test_store_identity.py`; `docs/operations.md`;
  `docs/requests/m5-store.md`. No migration: 0001 already holds everything the code needs.

## Decisions

- Ids leave the store as 32-hex strings (D47); an unusable owner id is `PolicyDenied`, an unusable row id `NotFound`.
- Postgres repositories rely on RLS for the filter (D129) and check parents explicitly, so a foreign id is
  `not_found` in both implementations. A foreign-key or RLS violation (SQLSTATE 23503, 42501) is also `not_found`.
- The identity is set with `set_config('milo.user_id', ..., true)`, the bound-parameter form of `SET LOCAL` (D74).
- One running turn per account, under an advisory lock; a turn older than 300 s is closed as stale (D131, D99).
- Approvals move along a fixed table; `used`, `kept` and `expired` are final, and a lapsed pending one is `expired` (D48).
- Trace and audit details reject owner-text keys and long strings (D141).
- Limits: lockout 5 failures per 900 s per account and 20 per address; sign-up 3 per email and 10 per address per
  hour; reset likewise. Keys are hashed. A locked account is refused before Supabase is asked (D92, D142).
- The consent chosen at sign-up rides in the Auth user metadata and is recorded at confirmation (D96).
- JWT: RS256 or ES256 only, algorithm must equal the one the JWK advertises, 60 s skew, `exp sub aud iss` required,
  anonymous tokens refused, the token's `role` is never used (D136, D144).

## Open items

See `docs/requests/m5-store.md`: Auth settings accessors, the `milo_operator` connection variable, `make migrate`
needing an administrative role, 0001 not being relocatable to a throwaway schema (the D77 flow cannot run as
written), and two Appendix A rows. The Postgres SQL has been checked only for parameter binding and call order, not executed.
