"""Profiles, customers and consents in Postgres (SPEC D81, D89, D90, D100).

A profile row is created by the trigger on `auth.users` when an account is confirmed; the app never inserts one and
cannot change `role` (the grants allow only `last_seen_at`). Every call runs under the owner's RLS identity.
"""

from __future__ import annotations

from typing import Any

from milo.errors import NotFound
from milo.store.common import (
    CONSENT_PURPOSES,
    check_choice,
    check_customer_fields,
    user_hex,
    uuid_text,
)
from milo.store.db import Database, fetch_all, fetch_one

PROFILE_COLS = "id, email, display_name, role, created_at, last_seen_at"
CUSTOMER_COLS = "user_id, company_name, industry, employee_count, locations, owner_name, owner_role, owner_email, created_at"
CONSENT_COLS = "id, user_id, purpose, version, at"

ENSURE_CUSTOMER = (
    "insert into customers (user_id) values (%s) on conflict (user_id) do nothing"
)


def ensure_customer(conn: Any, owner_id: str) -> None:
    conn.execute(ENSURE_CUSTOMER, (uuid_text(owner_id),))


class PgProfiles:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, owner_id: str) -> dict[str, Any]:
        who = user_hex(owner_id)
        row = self.db.one(
            who,
            f"select {PROFILE_COLS} from profiles where id = %s",
            (uuid_text(who),),
        )
        if row is None:
            raise NotFound()
        return row

    def role(self, owner_id: str) -> str:
        """Re-read on every operator request, never cached in the cookie (SPEC D144)."""
        return str(self.get(owner_id)["role"])

    def touch_last_seen(self, owner_id: str) -> None:
        who = user_hex(owner_id)
        if not self.db.run(
            who,
            "update profiles set last_seen_at = now() where id = %s",
            (uuid_text(who),),
        ):
            raise NotFound()


class PgCustomers:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, owner_id: str) -> dict[str, Any] | None:
        who = user_hex(owner_id)
        return self.db.one(
            who,
            f"select {CUSTOMER_COLS} from customers where user_id = %s",
            (uuid_text(who),),
        )

    def ensure(self, owner_id: str) -> dict[str, Any]:
        """The owner's customer row, created empty on first use. `NotFound` until the profile exists."""
        who = user_hex(owner_id)
        with self.db.transaction(who) as conn:
            ensure_customer(conn, who)
            row = fetch_one(
                conn,
                f"select {CUSTOMER_COLS} from customers where user_id = %s",
                (uuid_text(who),),
            )
        if row is None:
            raise NotFound()
        return row

    def update(self, owner_id: str, **fields: Any) -> dict[str, Any]:
        who = user_hex(owner_id)
        clean = check_customer_fields(fields)
        with self.db.transaction(who) as conn:
            ensure_customer(conn, who)
            if clean:
                sets = ", ".join(f"{name} = %s" for name in clean)
                conn.execute(
                    f"update customers set {sets} where user_id = %s",
                    (*clean.values(), uuid_text(who)),
                )
            row = fetch_one(
                conn,
                f"select {CUSTOMER_COLS} from customers where user_id = %s",
                (uuid_text(who),),
            )
        if row is None:
            raise NotFound()
        return row


class PgConsents:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(self, owner_id: str, purpose: str, version: str) -> dict[str, Any]:
        who = user_hex(owner_id)
        check_choice(purpose, CONSENT_PURPOSES, "consent_purpose")
        with self.db.transaction(who) as conn:
            ensure_customer(conn, who)
            row = fetch_one(
                conn,
                f"insert into consents (user_id, purpose, version) values (%s, %s, %s) returning {CONSENT_COLS}",
                (uuid_text(who), purpose, version),
            )
        if row is None:
            raise NotFound()
        return row

    def has(self, owner_id: str, purpose: str = "interview") -> bool:
        who = user_hex(owner_id)
        row = self.db.one(
            who,
            "select 1 as ok from consents where user_id = %s and purpose = %s limit 1",
            (uuid_text(who), purpose),
        )
        return row is not None

    def history(self, owner_id: str) -> list[dict[str, Any]]:
        who = user_hex(owner_id)
        with self.db.transaction(who) as conn:
            return fetch_all(
                conn,
                f"select {CONSENT_COLS} from consents where user_id = %s order by id",
                (uuid_text(who),),
            )
