# Requests from M5-store to the integrator

Integrator-owned paths: `src/milo/settings.py`, `src/milo/app.py`, `.env.example`, `pyproject.toml`. Nothing here
has been edited by M5-store. Each request names the test that will show it works.

## 1. `settings.py` accessors (SPEC D153)

`settings.py` is the only reader of the connection and Auth variables. The store takes URLs as arguments
(`Database(url, ...)`); `migrate.py` and `reset.py` already call `settings.admin_database_url` when it exists and
fall back to `settings.database_url` until then. Please add, each reading the env var for the current `MILO_ENV`:

Each returns the pooler string named in D153 for the current `MILO_ENV`:

- `admin_database_url(environ=None) -> str`: the administrative string (role `postgres`); migrate, db-reset and
  the RLS test only.
- `app_database_url(environ=None) -> str`: the `milo_app` string; `create_app` only.
- `operator_database_url(environ=None) -> str | None`: the `milo_operator` string in prod; `None` in test, where
  the operator path is `Database(admin_url, set_role="milo_operator")`.
- `auth_config(environ=None) -> AuthConfig`, a frozen dataclass of `url: str` (project base URL; `SupabaseAuth`
  appends `/auth/v1`), `publishable_key: str` (never the secret or service-role key), `jwks_url: str` and
  `issuer: str` (`f"{url}/auth/v1"`); the prod set only when `MILO_ENV=prod`.

`Database` modes: `Database(app_url)` (role must be `milo_app` or `milo_operator`), `Database(admin_url,
set_role="milo_app" | "milo_operator")` (runs `set local role` first in every transaction), and
`Database(admin_url, admin=True)` (migrate and reset only).

## 1b. `Makefile`

```make
db-reset:
	uv run python -m milo.store.reset
```

`reset.py` refuses `MILO_ENV=prod` outright. `make migrate` already refuses prod without `MILO_MIGRATE_PROD=1`.

## 2. `app.py`: wiring (M6 and M5b, at merge)

- `create_app` (lazy imports inside the function, so `import milo.app` stays driver-free):

  ```python
  db = Database(settings.database_url())
  db.open()
  repos = pg_repositories(db, Database(settings.operator_database_url()))
  cfg = settings.auth_config()
  service = LoginService(
      SupabaseAuth(cfg.url, cfg.publishable_key),
      JwtVerifier(issuer=cfg.issuer, jwks_url=cfg.jwks_url),
      RateLimiter(repos.rate_limits),
      repos,
  )
  ```

- `build_discovery_app(..., store=MemoryStore())`: `repos = memory_repositories(store)`; the same attribute
  names as the Postgres set.
- Per request: `claims = service.authenticate(token)`, owner id = `claims.sub`; `/ops` and `/traces` call
  `repos.profiles.role(owner)` every time (D144); the consent gate is `repos.consents.has(owner)` (D135).
- Error mapping: `NotFound` to 404 `not_found`; `TurnRunning` to 409; `RateLimited` to 429 `rate_limited` with
  `window_s` and `retry_after_s`; `AuthError` to 401; `PolicyDenied` to 403.
- Message and upload limits (D131): `RateLimiter.hit(ratelimit.MESSAGES, owner)` and `UPLOADS`.

## 3. Closed by D153 to D155

Migration 0001 is now idempotent in form, pins no `search_path`, and grants both roles to the administrative role; `migrate.py` uses the administrative URL only; `store/reset.py` implements `make db-reset`. The RLS test (`tests/test_store_identity.py --postgres`) runs on the administrative connection with `SET LOCAL ROLE`, never with passwords.

## 4. Appendix A rows

`docs/operations.md` (SPEC section 23 calls it an integrator file) and `docs/m5-store_report.md` were granted to
M5-store in the task but are not in the M5-store row of HARNESS Appendix A. Please add them before verification,
or the owned-path diff will flag them.
