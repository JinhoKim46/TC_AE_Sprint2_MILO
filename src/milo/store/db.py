"""Postgres access: one pooled connection per transaction, `milo.user_id` set per transaction (SPEC D74, D75, D129).

The app connects as `milo_app` (or `milo_operator` for operator pages), never as the Supabase service role.
Row-level security reads `current_setting('milo.user_id', true)`; this module sets it with
`set_config(..., true)`, which is transaction-local like `SET LOCAL` and takes a bound parameter.

The driver is imported inside `Database.__init__` only when no pool is injected, so importing this module (or
`milo.app`) loads no database driver (SPEC 11.12). Tests inject a fake pool and never open a socket.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from milo.errors import MiloError, NotFound
from milo.store.common import Conflict, user_hex, uuid_text

ALLOWED_ROLES = ("milo_app", "milo_operator")
SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
# SQLSTATEs that mean "this id is not yours or does not exist" or "already there".
_NOT_FOUND_STATES = frozenset({"23503", "42501"})
_CONFLICT_STATES = frozenset({"23505"})


def role_from_url(url: str) -> str:
    """The database role in a connection URL. The Supabase pooler writes it as `<role>.<project-ref>`."""
    user = urlsplit(url).username or ""
    return user.split(".")[0]


def check_role(url: str) -> str:
    """Refuse any role other than `milo_app` and `milo_operator` (SPEC D74). The message never holds the URL."""
    role = role_from_url(url)
    if role not in ALLOWED_ROLES:
        raise RuntimeError(
            "the store connects only as milo_app or milo_operator, never as an admin or service role"
        )
    return role


def norm_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return value.hex
    if isinstance(value, Decimal):
        return float(value)
    return value


def norm_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    return None if row is None else {k: norm_value(v) for k, v in row.items()}


def translate_error(exc: BaseException) -> BaseException:
    """Map foreign-key, row-level-security and unique violations to the typed errors; leave anything else."""
    if isinstance(exc, MiloError):
        return exc
    state = getattr(exc, "sqlstate", None)
    if state in _NOT_FOUND_STATES:
        return NotFound()
    if state in _CONFLICT_STATES:
        return Conflict()
    return exc


class Database:
    def __init__(
        self,
        url: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
        pool: Any = None,
        schema: str | None = None,
        set_role: str | None = None,
        admin: bool = False,
    ) -> None:
        """`schema` pins `search_path` for a throwaway schema (SPEC D77); the verifier's RLS run uses it."""
        if set_role is not None and set_role not in ALLOWED_ROLES:
            raise ValueError("set_role must be milo_app or milo_operator")
        # The app and operator URLs must name their own role (D74). The administrative URL (D153) is accepted only
        # when asked for: by the verifier's RLS run (`set_role`) or by migrate and reset (`admin=True`).
        if set_role is not None:
            self.role = set_role
        elif admin:
            self.role = "admin"
        else:
            self.role = check_role(url)
        self.set_role = set_role
        if schema is not None and not SCHEMA_RE.match(schema):
            raise ValueError("invalid schema name")
        if pool is None:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            pool = ConnectionPool(
                url,
                min_size=min_size,
                max_size=max_size,
                kwargs={
                    "row_factory": dict_row,
                    **(
                        {"options": f"-c search_path={schema},public"} if schema else {}
                    ),
                },
                open=False,
            )
        self._pool = pool

    def open(self, timeout: float = 15.0) -> None:
        self._pool.open(wait=True, timeout=timeout)

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def transaction(self, owner_id: str | None = None) -> Iterator[Any]:
        """One transaction. With an owner, the first statement sets the RLS identity for the whole transaction."""
        who = uuid_text(user_hex(owner_id)) if owner_id is not None else None
        try:
            with self._pool.connection() as conn:
                if (
                    self.set_role is not None
                ):  # transaction-local, so a pooled connection never keeps it
                    conn.execute(f"set local role {self.set_role}")
                if who is not None:
                    conn.execute("select set_config('milo.user_id', %s, true)", (who,))
                yield conn
        except BaseException as exc:
            err = translate_error(exc)
            if err is exc:
                raise
            raise err from exc

    # One-statement helpers. Rows come back as dicts with uuids as hex and numerics as floats.
    def one(
        self, owner_id: str | None, sql: str, params: Sequence[Any] = ()
    ) -> dict[str, Any] | None:
        with self.transaction(owner_id) as conn:
            return fetch_one(conn, sql, params)

    def all(
        self, owner_id: str | None, sql: str, params: Sequence[Any] = ()
    ) -> list[dict[str, Any]]:
        with self.transaction(owner_id) as conn:
            return fetch_all(conn, sql, params)

    def run(self, owner_id: str | None, sql: str, params: Sequence[Any] = ()) -> int:
        with self.transaction(owner_id) as conn:
            return int(conn.execute(sql, params).rowcount)


def fetch_one(conn: Any, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return norm_row(row)


def fetch_all(conn: Any, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    return [norm_row(r) or {} for r in conn.execute(sql, params).fetchall()]


def require_row(conn: Any, sql: str, params: Sequence[Any]) -> None:
    """Raise `NotFound` unless the (RLS-filtered) query returns a row."""
    if conn.execute(sql, params).fetchone() is None:
        raise NotFound()
