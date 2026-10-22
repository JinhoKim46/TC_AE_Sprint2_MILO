"""Apply `db/migrations/*.sql` in order and record each in `schema_migrations` (SPEC D73).

`make migrate` runs this module against the project chosen by `settings.database_url()` (the test project unless
`MILO_ENV=prod`). The driver is imported inside `apply_pending`, so importing this module opens nothing.
Tests use `plan()` only; they never open a database.
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path("db/migrations")

CREATE_TRACKING = """
create table if not exists schema_migrations (
    version    text primary key,
    checksum   text not null,
    applied_at timestamptz not null default now()
)
"""


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    checksum: str

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def discover(directory: Path | str = MIGRATIONS_DIR) -> list[Migration]:
    """Migration files in version order. The version is the file stem, e.g. `0001_init`."""
    out = []
    for path in sorted(Path(directory).glob("*.sql")):
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        out.append(Migration(path.stem, path, checksum))
    return out


def plan(applied: dict[str, str], migrations: Iterable[Migration]) -> list[Migration]:
    """Migrations still to apply. An applied migration whose file changed is an error, never re-applied."""
    pending = []
    for mig in migrations:
        if mig.version in applied:
            if applied[mig.version] != mig.checksum:
                raise RuntimeError(
                    f"migration {mig.version} changed after it was applied"
                )
            continue
        pending.append(mig)
    return pending


def apply_pending(url: str, directory: Path | str = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations, one transaction each. Returns the versions applied."""
    import psycopg

    done: list[str] = []
    with psycopg.connect(url, autocommit=False) as conn:
        conn.execute(CREATE_TRACKING)
        conn.commit()
        rows = conn.execute(
            "select version, checksum from schema_migrations"
        ).fetchall()
        applied = {str(r[0]): str(r[1]) for r in rows}
        for mig in plan(applied, discover(directory)):
            conn.execute(mig.sql)
            conn.execute(
                "insert into schema_migrations (version, checksum) values (%s, %s)",
                (mig.version, mig.checksum),
            )
            conn.commit()
            done.append(mig.version)
    return done


def main(argv: list[str] | None = None) -> int:
    from milo import settings

    args: Any = argv if argv is not None else sys.argv[1:]
    if args == ["--plan"]:
        for mig in discover():
            print(mig.version)
        return 0
    versions = apply_pending(settings.database_url())
    print("applied:", ", ".join(versions) if versions else "nothing to do")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
