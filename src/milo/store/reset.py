"""`make db-reset` (SPEC D154): drop every MILO object from the test project so it is empty again.

Test project only. Production is refused outright, with no override, and so is any process that holds production
settings. Connects with the administrative string (D153). The driver is imported inside `apply`.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from milo.store.migrate import MIGRATIONS_DIR, admin_url

FUNCTIONS = (
    "public.submissions_immutable()",
    "public.handle_confirmed_user()",
    "public.milo_user()",
    "public.owns_session(uuid)",
    "public.owns_turn(uuid)",
)
ROLES = ("milo_app", "milo_operator")


def refuse_prod(environ: Mapping[str, str] | None = None) -> None:
    from milo import settings

    env = os.environ if environ is None else environ
    if settings.is_prod(env):
        raise RuntimeError("db-reset refuses production, with no override")


def tables(directory: Path | str = MIGRATIONS_DIR) -> list[str]:
    """Every table a migration creates, read from the migration files so a new table is never forgotten."""
    names: list[str] = []
    for path in sorted(Path(directory).glob("*.sql")):
        names += re.findall(
            r"^create table (?:if not exists )?(\w+) \(",
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    return list(dict.fromkeys([*names, "schema_migrations"]))


def statements(directory: Path | str = MIGRATIONS_DIR) -> list[str]:
    out = ["drop trigger if exists on_auth_user_confirmed on auth.users"]
    out += [
        f"drop table if exists public.{t} cascade" for t in reversed(tables(directory))
    ]
    out += [f"drop function if exists {f} cascade" for f in FUNCTIONS]
    for role in ROLES:
        out.append(
            f"do $$ begin if exists (select 1 from pg_roles where rolname = '{role}') then "
            f"drop owned by {role}; drop role {role}; end if; end $$"
        )
    return out


def apply(url: str, directory: Path | str = MIGRATIONS_DIR) -> int:
    import psycopg

    stmts = statements(directory)
    with psycopg.connect(url, autocommit=False) as conn:
        for sql in stmts:
            conn.execute(sql)
        conn.commit()
    return len(stmts)


def main() -> int:
    refuse_prod()
    count = apply(admin_url())
    print(f"db-reset: ran {count} statements; the test project holds no MILO objects")
    return 0


if __name__ == "__main__":
    sys.exit(main())
