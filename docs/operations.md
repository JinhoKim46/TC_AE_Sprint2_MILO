# Operations: the owner's manual steps

These steps are done by hand, by the owner, in the Supabase dashboard and its SQL editor. Code never does them,
and no secret is written to the repository, to a commit or to a chat. Do each step once for the test project and
once, later, for the production project. Variable names are listed in `.env.example`; this page refers to them
by purpose.

## 1. Authentication settings in the dashboard (SPEC D95)

In the project's dashboard, under Authentication:

1. Providers, Email: turn on "Confirm email". Email and password sign-in stays on. Other providers stay off.
2. Sign-in and sign-up settings: set the minimum password length to 12 (the app also checks it, D92).
3. URL configuration: set the site URL to the app address (the local address for the test project, the real
   domain for production) and add the same address's `/confirm` and `/reset` paths to the redirect URLs.
4. Rate limits: read the email rate limits for the free tier. The app counts its own limits too (D92, D131),
   but the dashboard limit on confirmation and reset emails is the one that stops a mail flood.
5. Optional, owner decision: multi-factor authentication for the operator account (checklist item 8, D144).

Copy three public values into `.env` (git-ignored): the project's Auth URL, its publishable key and its JWKS
URL. The app never uses the secret key or the service-role key (D74, D88); leave them out of `.env`.

## 2. Apply the schema

`make migrate` applies `db/migrations/*.sql` in order and records them in `schema_migrations`. Migration 0001
creates the roles `milo_app` and `milo_operator` and every table with its row-level security policies.

`make migrate` uses the administrative session-pooler string only (role `postgres`, SPEC D153); the app never
does. It refuses production unless you set `MILO_MIGRATE_PROD=1` by hand. In the test project no role passwords
are needed: the migration lets the administrative role `SET ROLE` to both app roles. `make db-reset` empties the
test project again (never production). Never commit or paste a connection string.

## 3. Set the passwords of `milo_app` and `milo_operator`

The migration creates both roles with login but no password. In the SQL editor, as the administrative role:

```sql
alter role milo_app with password '<a long random password>';
alter role milo_operator with password '<another long random password>';
```

Build the session-pooler URLs with the user names `milo_app.<project-ref>` and `milo_operator.<project-ref>`
and put the `milo_app` one in `.env` (the operator URL is listed as an open item in `docs/m5-store_report.md`).
The Supabase service role must never appear in the app's connection string: `milo.store.db.Database` refuses
any role other than these two.

## 4. Create the first operator (SPEC D94)

No sign-up path can create an operator. After the person has signed up and confirmed their email, run in the
SQL editor as the administrative role:

```sql
update public.profiles set role = 'operator' where email = '<their email address>';
```

Check it with `select id, email, role from public.profiles where role = 'operator';`. The app re-reads the role
from the database on every `/ops` and `/traces` request (D144), so the change takes effect at once and a
revoked operator loses access on the next request. To revoke, set `role = 'owner'`.

## 5. Check row-level security (the verifier's run, SPEC D140)

With a throwaway schema, two confirmed test accounts and the test project's `milo_app` URL in `.env`:

```text
uv run python tests/test_store_identity.py --postgres --owner-a <uuid> --owner-b <uuid> [--schema milo_<hex>]
```

It refuses production settings, seeds owner A, makes every call as owner B with A's ids and prints PASS or FAIL
per call. Drop the schema afterwards (D77).

## 6. Before production

- Real consent text replaces the placeholder `v0` (D81); the owner reviews it.
- Production refuses to start with a short or example `MILO_SECRET_KEY`, or with `FLASK_DEBUG` set (D137).
- Tests and evals never run with the production variables set (D76).
