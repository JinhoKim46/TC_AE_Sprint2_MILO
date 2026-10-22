# Requests from M5-store to the integrator

Integrator-owned paths: `src/milo/settings.py`, `src/milo/app.py`, `.env.example`, `pyproject.toml`. Nothing here
has been edited by M5-store. Each request names the test that will show it works.

## 1. `settings.py`: Auth configuration and the operator database URL

`milo.auth` takes the project's Auth URL, publishable key and JWKS URL as arguments, because only `settings.py`
may read the variables (HARNESS 3.3). Please add, next to `database_url()`:

```python
@dataclass(frozen=True)
class AuthConfig:
    url: str  # the project's base URL; SupabaseAuth appends /auth/v1
    publishable_key: str  # never the secret or service-role key
    jwks_url: str  # JwtVerifier(jwks_url=...) fetches it over https
    issuer: str  # f"{url}/auth/v1"


def auth_config(
    environ=None,
) -> AuthConfig: ...  # test project unless MILO_ENV=prod, like database_url()
def operator_database_url(
    environ=None,
) -> str: ...  # a milo_operator URL for /ops (SPEC D130, D144)
```

and the matching placeholder lines in `.env.example` (D88 names `SUPABASE_*_JWKS_URL`; the exact names are yours).
SPEC D127 lists only the two `milo_app` pooler variables, so there is no variable for `milo_operator` yet.
Test: `tests/test_s0_smoke.py::test_env_example_has_placeholders_only` still passes, and a new settings test
asserts `auth_config({...})` picks the prod set only when `MILO_ENV=prod`.

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

## 3. `make migrate` and the administrative role

`src/milo/store/migrate.py` connects with `settings.database_url()`, which is a `milo_app` URL, but migration
0001 creates roles and policies and needs an administrative role. `docs/operations.md` section 2 gives the manual
workaround (temporarily point the variable at the administrative URL). A cleaner fix is a third variable for
migrations, or a documented rule that `make migrate` is run by the owner only.

## 4. Migration 0001 cannot run in a throwaway schema (blocks HARNESS 7 item 8, SPEC D77)

The verifier flow (create schema `milo_<hex>`, `make migrate`, run the RLS test, drop the schema) does not work
with 0001 and `migrate.py` as they are:

- `migrate.py` has no `search_path` option, so `make migrate` always targets `public`.
- `owns_session` and `owns_turn` declare `set search_path = public`, so in another schema they read
  `public.sessions` and `public.turns` (the wrong tables).
- `grant usage on schema public`, `revoke all on all tables in schema public` and `grant select on all tables
  in schema public` are hardcoded, so in another schema the grants land on the wrong tables or on nothing.
- `public.handle_confirmed_user()` is `create function` (not `or replace`) and `on_auth_user_confirmed` is
  `create trigger` on `auth.users`, both outside the schema. A second throwaway schema, or a rerun on the test
  project, fails at them, and `drop schema milo_<hex> cascade` leaves both behind. Roles are shared and
  persist, which is fine, but the trigger and function are not.
- Nearly every `create table`, `create function` and `create policy` lacks `if not exists` / `or replace`.

Options for the integrator: (a) run the RLS test in the dedicated test project's `public` schema, resetting it
between runs; (b) make 0001 schema-relocatable (unqualified grants through `current_schema()`, helpers without a
pinned `search_path`, trigger and function created `or replace` / dropped first) and add `--schema` to
`migrate.py`. `Database(url, schema=...)` in `milo.store.db` already pins `search_path` on the app side.
Do not add a 0002 on top of a 0001 that fails first.

## 5. No connection variable for `milo_operator`

SPEC D127 lists two variables, both `milo_app`. `pg_repositories(db)` without `operator_db` routes `audit` and
`operator` through the `milo_app` connection, and `milo_app` has no grant on `audit_log` or `customers` delete,
so those calls fail with a permission error until request 1 supplies an operator URL.

## 6. Appendix A rows

`docs/operations.md` (SPEC section 23 calls it an integrator file) and `docs/m5-store_report.md` were granted to
M5-store in the task but are not in the M5-store row of HARNESS Appendix A. Please add them before verification,
or the owned-path diff will flag them.
