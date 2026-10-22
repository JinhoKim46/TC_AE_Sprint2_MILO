"""In-memory store (SPEC D70, D74). No database, no network: tests and discovery mode use it.

Every owner-facing call takes the owner id and applies the same owner filter that row-level security applies in
Postgres: a row of another owner is invisible (`get` returns None, `select` skips it, `update` and `delete` raise
`NotFound`). The store role (M5-store) builds the typed repositories on top of this.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from milo.errors import InvalidInput, NotFound, PolicyDenied
from milo.store.common import (
    APPROVAL_STATUSES,
    APPROVAL_TRANSITIONS,
    CONSENT_PURPOSES,
    EVIDENCE_KINDS,
    FACT_STATUSES,
    MESSAGE_ROLES,
    SESSION_STATUSES,
    STALE_TURN_S,
    SUBMISSION_STATUSES,
    TURN_FINISH_STATUSES,
    TurnRunning,
    check_choice,
    check_customer_fields,
    check_trace_detail,
    clean_title,
    uid,
    user_hex,
)

APPROVAL_TTL_S = 600.0


def args_hash(args: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(args, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


class MemoryStore:
    """Generic owner-scoped tables, plus the `rate_limits` counter (SPEC D142)."""

    def __init__(self) -> None:
        self._tables: dict[str, dict[int, dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._limits: dict[tuple[str, str, int], int] = {}
        self._limit_starts: dict[tuple[str, str, int], float] = {}

    def insert(self, table: str, owner_id: str, row: dict[str, Any]) -> int:
        row_id = next(self._ids)
        self._tables.setdefault(table, {})[row_id] = {
            **row,
            "id": row_id,
            "owner_id": owner_id,
        }
        return row_id

    def get(self, table: str, owner_id: str, row_id: int) -> dict[str, Any] | None:
        row = self._tables.get(table, {}).get(row_id)
        return copy.deepcopy(row) if row and row["owner_id"] == owner_id else None

    def select(self, table: str, owner_id: str) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(r)
            for r in self._tables.get(table, {}).values()
            if r["owner_id"] == owner_id
        ]

    def update(
        self, table: str, owner_id: str, row_id: int, changes: dict[str, Any]
    ) -> None:
        row = self._tables.get(table, {}).get(row_id)
        if not row or row["owner_id"] != owner_id:
            raise NotFound(table)
        row.update({k: v for k, v in changes.items() if k not in ("id", "owner_id")})

    def delete(self, table: str, owner_id: str, row_id: int) -> None:
        row = self._tables.get(table, {}).get(row_id)
        if not row or row["owner_id"] != owner_id:
            raise NotFound(table)
        del self._tables[table][row_id]

    def hit(
        self, scope: str, key: str, *, window_s: int, now: float | None = None
    ) -> int:
        """Count one event in the current window and return the count (the `rate_limits` table in Postgres)."""
        t = time.time() if now is None else now
        slot = (scope, key, int(t // window_s))
        self._limits[slot] = self._limits.get(slot, 0) + 1
        self._limit_starts[slot] = slot[2] * window_s
        return self._limits[slot]

    def peek(
        self, scope: str, key: str, *, window_s: int, now: float | None = None
    ) -> int:
        t = time.time() if now is None else now
        return self._limits.get((scope, key, int(t // window_s)), 0)

    def reset_limit(self, scope: str, key: str) -> None:
        for slot in [s for s in self._limits if s[0] == scope and s[1] == key]:
            del self._limits[slot]
            self._limit_starts.pop(slot, None)

    def prune_limits(self, older_than_s: float, now: float | None = None) -> int:
        """Drop counters whose window started more than `older_than_s` ago."""
        t = time.time() if now is None else now
        old = [s for s, start in self._limit_starts.items() if start < t - older_than_s]
        for slot in old:
            del self._limits[slot]
            del self._limit_starts[slot]
        return len(old)

    def purge_owner(self, owner_id: str, keep: tuple[str, ...] = ("profiles",)) -> None:
        """Remove an owner's rows, like `on delete cascade` from `customers` in Postgres (the profile stays)."""
        for table, rows in self._tables.items():
            if table in keep:
                continue
            for row_id in [i for i, r in rows.items() if r["owner_id"] == owner_id]:
                del rows[row_id]

    def snapshot(self) -> dict[str, Any]:
        """A deep copy of all state, so a test can prove that a refused call changed nothing."""
        return copy.deepcopy(
            {
                "tables": self._tables,
                "limits": self._limits,
                "starts": self._limit_starts,
            }
        )


class MemoryApprovals:
    """Approval lifecycle storage for contract mode (SPEC section 8, D48).

    Statuses: pending, approved, kept, used, expired. A row binds (scope, turn, tool, argument hash) to its owner;
    another owner reading it gets None, which the caller reports as `not_found`.
    """

    def __init__(
        self, ttl_s: float = APPROVAL_TTL_S, clock: Callable[[], float] = time.time
    ) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._ttl = ttl_s
        self._clock = clock

    def create(
        self,
        *,
        scope: str,
        owner_id: str,
        turn_id: str,
        tool_name: str,
        args: dict[str, Any],
        context: Any = None,
    ) -> str:
        approval_id = uuid.uuid4().hex
        now = self._clock()
        self._rows[approval_id] = {
            "id": approval_id,
            "scope": scope,
            "owner_id": owner_id,
            "turn_id": turn_id,
            "tool_name": tool_name,
            "args": dict(args),
            "args_hash": args_hash(args),
            "status": "pending",
            "created_at": now,
            "expires_at": now + self._ttl,
            "context": context,
        }
        return approval_id

    def get(self, approval_id: str, owner_id: str) -> dict[str, Any] | None:
        row = self._rows.get(approval_id)
        if not row or row["owner_id"] != owner_id:
            return None
        if row["status"] == "pending" and self._clock() >= row["expires_at"]:
            row["status"] = "expired"
        return dict(row)

    def set_status(self, approval_id: str, owner_id: str, status: str) -> None:
        row = self._rows.get(approval_id)
        if not row or row["owner_id"] != owner_id:
            raise NotFound("approval")
        row["status"] = status


# ---------------------------------------------------------------------------------------------------------------
# Typed repositories over `MemoryStore` (SPEC D70, D74). Each class mirrors the Postgres class of the same topic
# (`Pg<Name>` in `users.py`, `sessions.py`, ...): same method names, same signatures, same errors. Every call
# resolves its rows through the owner filter of `MemoryStore`, so another owner's id is `NotFound`.
# ---------------------------------------------------------------------------------------------------------------

SYSTEM_OWNER = "-"  # owner column of rows that belong to nobody (audit log)


class _Base:
    def __init__(
        self, store: MemoryStore, clock: Callable[[], float] = time.time
    ) -> None:
        self.store = store
        self.clock = clock

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), tz=UTC)

    def find(self, table: str, owner: str, **where: Any) -> dict[str, Any] | None:
        for row in self.store.select(table, owner):
            if all(row.get(k) == v for k, v in where.items()):
                return row
        return None

    def need(self, table: str, owner: str, **where: Any) -> dict[str, Any]:
        row = self.find(table, owner, **where)
        if row is None:
            raise NotFound()
        return row

    def session(self, owner: str, session_id: str) -> dict[str, Any]:
        return self.need("sessions", owner, uuid=uid(session_id))


def _public(row: dict[str, Any], **extra: Any) -> dict[str, Any]:
    out = {k: v for k, v in row.items() if k not in ("owner_id", "uuid")}
    out.update(extra)
    return out


class MemoryProfiles(_Base):
    def create_confirmed(
        self, user_id: str, email: str | None = None
    ) -> dict[str, Any]:
        """Stand-in for the `auth.users` trigger of D89, which inserts the profile on confirmation."""
        who = user_hex(user_id)
        row = self.find("profiles", who)
        if row is None:
            self.store.insert(
                "profiles",
                who,
                {
                    "email": email,
                    "display_name": None,
                    "role": "owner",
                    "created_at": self.now(),
                    "last_seen_at": None,
                },
            )
        return self.get(who)

    def set_role_for_test(self, user_id: str, role: str) -> None:
        """What an operator does by hand in the database (SPEC D94). Not part of the app surface."""
        who = user_hex(user_id)
        self.store.update(
            "profiles", who, self.need("profiles", who)["id"], {"role": role}
        )

    def get(self, owner_id: str) -> dict[str, Any]:
        who = user_hex(owner_id)
        row = self.need("profiles", who)
        return {**_public(row), "id": who}

    def role(self, owner_id: str) -> str:
        return str(self.get(owner_id)["role"])

    def touch_last_seen(self, owner_id: str) -> None:
        who = user_hex(owner_id)
        self.store.update(
            "profiles",
            who,
            self.need("profiles", who)["id"],
            {"last_seen_at": self.now()},
        )


class MemoryCustomers(_Base):
    def _ensure(self, who: str) -> dict[str, Any]:
        if self.find("profiles", who) is None:
            raise NotFound()
        row = self.find("customers", who)
        if row is None:
            fields: dict[str, Any] = {
                "company_name": None,
                "industry": None,
                "employee_count": None,
                "locations": None,
                "owner_name": None,
                "owner_role": None,
                "owner_email": None,
                "created_at": self.now(),
            }
            self.store.insert("customers", who, fields)
            row = self.need("customers", who)
        return row

    @staticmethod
    def _out(row: dict[str, Any], who: str) -> dict[str, Any]:
        out = _public(row)
        out.pop("id", None)
        return {"user_id": who, **out}

    def get(self, owner_id: str) -> dict[str, Any] | None:
        who = user_hex(owner_id)
        row = self.find("customers", who)
        return None if row is None else self._out(row, who)

    def ensure(self, owner_id: str) -> dict[str, Any]:
        who = user_hex(owner_id)
        return self._out(self._ensure(who), who)

    def update(self, owner_id: str, **fields: Any) -> dict[str, Any]:
        who = user_hex(owner_id)
        clean = check_customer_fields(fields)
        row = self._ensure(who)
        if clean:
            self.store.update("customers", who, row["id"], clean)
        return self._out(self.need("customers", who), who)


class MemoryConsents(_Base):
    def record(self, owner_id: str, purpose: str, version: str) -> dict[str, Any]:
        who = user_hex(owner_id)
        check_choice(purpose, CONSENT_PURPOSES, "consent_purpose")
        MemoryCustomers(self.store, self.clock).ensure(who)
        rid = self.store.insert(
            "consents", who, {"purpose": purpose, "version": version, "at": self.now()}
        )
        return self._out(self.need("consents", who, id=rid), who)

    @staticmethod
    def _out(row: dict[str, Any], who: str) -> dict[str, Any]:
        return {
            "id": row["id"],
            "user_id": who,
            "purpose": row["purpose"],
            "version": row["version"],
            "at": row["at"],
        }

    def has(self, owner_id: str, purpose: str = "interview") -> bool:
        who = user_hex(owner_id)
        return self.find("consents", who, purpose=purpose) is not None

    def history(self, owner_id: str) -> list[dict[str, Any]]:
        who = user_hex(owner_id)
        return [
            self._out(r, who)
            for r in sorted(self.store.select("consents", who), key=lambda r: r["id"])
        ]


class MemorySessions(_Base):
    def _out(self, row: dict[str, Any], who: str) -> dict[str, Any]:
        return {
            "id": row["uuid"],
            "user_id": who,
            "title": row["title"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "archived_at": row["archived_at"],
        }

    def create(self, owner_id: str, title: str | None = None) -> dict[str, Any]:
        who = user_hex(owner_id)
        name = clean_title(title)
        MemoryCustomers(self.store, self.clock).ensure(who)
        now = self.now()
        self.store.insert(
            "sessions",
            who,
            {
                "uuid": uuid.uuid4().hex,
                "title": name,
                "status": "active",
                "created_at": now,
                "updated_at": now,
                "archived_at": None,
            },
        )
        rows = sorted(self.store.select("sessions", who), key=lambda r: r["id"])
        return self._out(rows[-1], who)

    def get(self, owner_id: str, session_id: str) -> dict[str, Any]:
        who = user_hex(owner_id)
        return self._out(self.session(who, session_id), who)

    def search(
        self,
        owner_id: str,
        *,
        status: str | None = None,
        query: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        who = user_hex(owner_id)
        rows = self.store.select("sessions", who)
        if status is None:
            rows = [r for r in rows if r["status"] != "archived"]
        else:
            check_choice(status, SESSION_STATUSES, "session_status")
            rows = [r for r in rows if r["status"] == status]
        if query:
            rows = [r for r in rows if query.lower() in (r["title"] or "").lower()]
        rows.sort(key=lambda r: (-r["updated_at"].timestamp(), r["id"]))
        return [self._out(r, who) for r in rows[: max(1, min(limit, 500))]]

    def _change(self, who: str, session_id: str, **changes: Any) -> dict[str, Any]:
        row = self.session(who, session_id)
        self.store.update(
            "sessions", who, row["id"], {"updated_at": self.now(), **changes}
        )
        return self._out(self.session(who, session_id), who)

    def rename(self, owner_id: str, session_id: str, title: str) -> dict[str, Any]:
        who = user_hex(owner_id)
        name = clean_title(title)
        if name is None:
            raise NotFound()
        return self._change(who, session_id, title=name)

    def archive(self, owner_id: str, session_id: str) -> dict[str, Any]:
        who = user_hex(owner_id)
        row = self.session(who, session_id)
        if row["status"] == "archived":
            return self._out(row, who)
        return self._change(who, session_id, status="archived", archived_at=self.now())

    def restore(self, owner_id: str, session_id: str) -> dict[str, Any]:
        who = user_hex(owner_id)
        row = self.session(who, session_id)
        if row["status"] != "archived":
            return self._out(row, who)
        submitted = any(
            r["session_id"] == row["uuid"]
            for r in self.store.select("submissions", who)
        )
        return self._change(
            who,
            session_id,
            status="submitted" if submitted else "active",
            archived_at=None,
        )

    def set_status(self, owner_id: str, session_id: str, status: str) -> dict[str, Any]:
        who = user_hex(owner_id)
        check_choice(status, SESSION_STATUSES, "session_status")
        row = self.session(who, session_id)
        archived_at = (
            (row["archived_at"] or self.now()) if status == "archived" else None
        )
        return self._change(who, session_id, status=status, archived_at=archived_at)

    def touch(self, owner_id: str, session_id: str) -> None:
        who = user_hex(owner_id)
        self._change(who, session_id)


class MemoryTurns(_Base):
    def __init__(
        self,
        store: MemoryStore,
        clock: Callable[[], float] = time.time,
        stale_s: float = STALE_TURN_S,
    ) -> None:
        super().__init__(store, clock)
        self.stale_s = stale_s

    def _out(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["uuid"],
            "session_id": row["session_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }

    def _turn(self, who: str, turn_id: str) -> dict[str, Any]:
        return self.need("turns", who, uuid=uid(turn_id))

    def start(self, owner_id: str, session_id: str) -> dict[str, Any]:
        who = user_hex(owner_id)
        session = self.session(who, session_id)
        now = self.now()
        for row in self.store.select("turns", who):
            if (
                row["status"] == "running"
                and (now - row["created_at"]).total_seconds() > self.stale_s
            ):
                self.store.update(
                    "turns", who, row["id"], {"status": "error", "finished_at": now}
                )
        if session["status"] == "archived":
            raise PolicyDenied("session_archived")
        if self.find("turns", who, status="running") is not None:
            raise TurnRunning()
        tid = uuid.uuid4().hex
        self.store.insert(
            "turns",
            who,
            {
                "uuid": tid,
                "session_id": session["uuid"],
                "status": "running",
                "created_at": now,
                "finished_at": None,
            },
        )
        return self._out(self._turn(who, tid))

    def get(self, owner_id: str, turn_id: str) -> dict[str, Any]:
        who = user_hex(owner_id)
        return self._out(self._turn(who, turn_id))

    def finish(
        self, owner_id: str, turn_id: str, status: str = "done"
    ) -> dict[str, Any]:
        who = user_hex(owner_id)
        check_choice(status, TURN_FINISH_STATUSES, "turn_status")
        row = self._turn(who, turn_id)
        if row["status"] == "running":
            self.store.update(
                "turns", who, row["id"], {"status": status, "finished_at": self.now()}
            )
        return self._out(self._turn(who, turn_id))

    def add_event(
        self, owner_id: str, turn_id: str, type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        who = user_hex(owner_id)
        turn = self._turn(who, turn_id)
        seq = 1 + sum(
            1
            for r in self.store.select("turn_events", who)
            if r["turn_id"] == turn["uuid"]
        )
        rid = self.store.insert(
            "turn_events",
            who,
            {
                "turn_id": turn["uuid"],
                "seq": seq,
                "type": type,
                "payload_json": copy.deepcopy(payload),
                "created_at": self.now(),
            },
        )
        return self._event(self.need("turn_events", who, id=rid))

    @staticmethod
    def _event(row: dict[str, Any]) -> dict[str, Any]:
        return _public(row)

    def events(self, owner_id: str, turn_id: str) -> list[dict[str, Any]]:
        who = user_hex(owner_id)
        turn = self._turn(who, turn_id)
        rows = [
            r
            for r in self.store.select("turn_events", who)
            if r["turn_id"] == turn["uuid"]
        ]
        return [self._event(r) for r in sorted(rows, key=lambda r: r["seq"])]


class MemoryMessages(_Base):
    def add(
        self,
        owner_id: str,
        session_id: str,
        role: str,
        content: str,
        trace_hash: str | None = None,
    ) -> dict[str, Any]:
        who = user_hex(owner_id)
        session = self.session(who, session_id)
        check_choice(role, MESSAGE_ROLES, "message_role")
        if not content or not content.strip():
            raise InvalidInput("empty")
        rid = self.store.insert(
            "messages",
            who,
            {
                "session_id": session["uuid"],
                "role": role,
                "content": content,
                "created_at": self.now(),
                "trace_hash": trace_hash,
            },
        )
        self.store.update("sessions", who, session["id"], {"updated_at": self.now()})
        return _public(self.need("messages", who, id=rid))

    def get(self, owner_id: str, session_id: str, message_id: int) -> dict[str, Any]:
        who = user_hex(owner_id)
        sid = self.session(who, session_id)["uuid"]
        return _public(self.need("messages", who, id=int(message_id), session_id=sid))

    def history(
        self,
        owner_id: str,
        session_id: str,
        *,
        after_id: int = 0,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        who = user_hex(owner_id)
        sid = self.session(who, session_id)["uuid"]
        rows = sorted(
            (
                r
                for r in self.store.select("messages", who)
                if r["session_id"] == sid and r["id"] > int(after_id)
            ),
            key=lambda r: r["id"],
        )
        if limit is not None:
            rows = rows[: max(1, int(limit))]
        return [_public(r) for r in rows]


class MemoryFacts(_Base):
    def upsert(
        self,
        owner_id: str,
        session_id: str,
        field_id: str,
        *,
        value: Any,
        status: str,
        unit: str | None = None,
        period: str | None = None,
        source_message_id: int | None = None,
    ) -> dict[str, Any]:
        who = user_hex(owner_id)
        sid = self.session(who, session_id)["uuid"]
        check_choice(status, FACT_STATUSES, "fact_status")
        if source_message_id is not None:
            self.need("messages", who, id=int(source_message_id), session_id=sid)
        now = self.now()
        fields = {
            "value_json": copy.deepcopy(value),
            "unit": unit,
            "period": period,
            "status": status,
            "source_message_id": source_message_id,
            "updated_at": now,
        }
        row = self.find("facts", who, session_id=sid, field_id=field_id)
        if row is None:
            self.store.insert(
                "facts", who, {"session_id": sid, "field_id": field_id, **fields}
            )
        else:
            self.store.update("facts", who, row["id"], fields)
        self.store.insert(
            "fact_history",
            who,
            {
                "session_id": sid,
                "field_id": field_id,
                "value_json": copy.deepcopy(value),
                "status": status,
                "source_message_id": source_message_id,
                "changed_at": now,
            },
        )
        return self._fact(self.need("facts", who, session_id=sid, field_id=field_id))

    @staticmethod
    def _fact(row: dict[str, Any]) -> dict[str, Any]:
        out = _public(row)
        out.pop("id", None)
        return out

    def get(self, owner_id: str, session_id: str, field_id: str) -> dict[str, Any]:
        who = user_hex(owner_id)
        sid = self.session(who, session_id)["uuid"]
        return self._fact(self.need("facts", who, session_id=sid, field_id=field_id))

    def for_session(self, owner_id: str, session_id: str) -> list[dict[str, Any]]:
        who = user_hex(owner_id)
        sid = self.session(who, session_id)["uuid"]
        rows = [r for r in self.store.select("facts", who) if r["session_id"] == sid]
        return [self._fact(r) for r in sorted(rows, key=lambda r: r["field_id"])]

    def field_history(
        self, owner_id: str, session_id: str, field_id: str
    ) -> list[dict[str, Any]]:
        who = user_hex(owner_id)
        sid = self.session(who, session_id)["uuid"]
        rows = [
            r
            for r in self.store.select("fact_history", who)
            if r["session_id"] == sid and r["field_id"] == field_id
        ]
        return [_public(r) for r in sorted(rows, key=lambda r: r["id"])]


class MemoryEvidence(_Base):
    def add(
        self,
        owner_id: str,
        session_id: str,
        kind: str,
        *,
        ref: str | None = None,
        score: float | None = None,
        text_hash: str | None = None,
        message_id: int | None = None,
    ) -> dict[str, Any]:
        who = user_hex(owner_id)
        sid = self.session(who, session_id)["uuid"]
        check_choice(kind, EVIDENCE_KINDS, "evidence_kind")
        if message_id is not None:
            self.need("messages", who, id=int(message_id), session_id=sid)
        rid = self.store.insert(
            "evidence",
            who,
            {
                "session_id": sid,
                "message_id": message_id,
                "kind": kind,
                "ref": ref,
                "score": score,
                "text_hash": text_hash,
                "created_at": self.now(),
            },
        )
        return _public(self.need("evidence", who, id=rid))

    def for_session(self, owner_id: str, session_id: str) -> list[dict[str, Any]]:
        who = user_hex(owner_id)
        sid = self.session(who, session_id)["uuid"]
        rows = [r for r in self.store.select("evidence", who) if r["session_id"] == sid]
        return [_public(r) for r in sorted(rows, key=lambda r: r["id"])]


class MemorySubmissions(_Base):
    def create(
        self, owner_id: str, session_id: str, spec_snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        who = user_hex(owner_id)
        session = self.session(who, session_id)
        revision = 1 + sum(
            1
            for r in self.store.select("submissions", who)
            if r["session_id"] == session["uuid"]
        )
        rid = self.store.insert(
            "submissions",
            who,
            {
                "session_id": session["uuid"],
                "revision": revision,
                "submitted_at": self.now(),
                "status": "received",
                "spec_snapshot_json": copy.deepcopy(spec_snapshot),
                "report_json": None,
                "report_html": None,
                "estimate_status": None,
                "cost_low": None,
                "cost_base": None,
                "cost_high": None,
                "currency": None,
                "rate_card_version": None,
                "model_id": None,
            },
        )
        if session["status"] == "active":
            self.store.update(
                "sessions",
                who,
                session["id"],
                {"status": "submitted", "updated_at": self.now()},
            )
        return _public(self.need("submissions", who, id=rid))

    def get(self, owner_id: str, submission_id: int) -> dict[str, Any]:
        who = user_hex(owner_id)
        return _public(self.need("submissions", who, id=int(submission_id)))

    def for_session(self, owner_id: str, session_id: str) -> list[dict[str, Any]]:
        who = user_hex(owner_id)
        sid = self.session(who, session_id)["uuid"]
        rows = [
            r for r in self.store.select("submissions", who) if r["session_id"] == sid
        ]
        return [_public(r) for r in sorted(rows, key=lambda r: r["revision"])]

    def latest(self, owner_id: str, session_id: str) -> dict[str, Any] | None:
        rows = self.for_session(owner_id, session_id)
        return rows[-1] if rows else None

    def attach_report(
        self,
        owner_id: str,
        submission_id: int,
        *,
        report_json: dict[str, Any],
        report_html: str | None = None,
        estimate_status: str | None = None,
        cost_low: float | None = None,
        cost_base: float | None = None,
        cost_high: float | None = None,
        currency: str | None = None,
        rate_card_version: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        who = user_hex(owner_id)
        row = self.need("submissions", who, id=int(submission_id))
        if row["report_json"] is not None:
            raise PolicyDenied("report_already_set")
        self.store.update(
            "submissions",
            who,
            row["id"],
            {
                "report_json": copy.deepcopy(report_json),
                "report_html": report_html,
                "estimate_status": estimate_status,
                "cost_low": cost_low,
                "cost_base": cost_base,
                "cost_high": cost_high,
                "currency": currency,
                "rate_card_version": rate_card_version,
                "model_id": model_id,
                "status": "report_ready",
            },
        )
        return self.get(who, submission_id)

    def set_status(
        self, owner_id: str, submission_id: int, status: str
    ) -> dict[str, Any]:
        who = user_hex(owner_id)
        check_choice(status, SUBMISSION_STATUSES, "submission_status")
        row = self.need("submissions", who, id=int(submission_id))
        self.store.update("submissions", who, row["id"], {"status": status})
        return self.get(who, submission_id)


class MemoryApprovalRows(_Base):
    """The `approvals` table in memory (discovery mode). Contract mode keeps using `MemoryApprovals` above."""

    def __init__(
        self,
        store: MemoryStore,
        clock: Callable[[], float] = time.time,
        ttl_s: float = APPROVAL_TTL_S,
    ) -> None:
        super().__init__(store, clock)
        self.ttl_s = ttl_s

    def create(
        self,
        owner_id: str,
        session_id: str,
        tool_name: str,
        args: dict[str, Any],
        *,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        who = user_hex(owner_id)
        session = self.session(who, session_id)
        tid = None
        if turn_id is not None:
            tid = self.need(
                "turns", who, uuid=uid(turn_id), session_id=session["uuid"]
            )["uuid"]
        now = self.now()
        rid = self.store.insert(
            "approvals",
            who,
            {
                "session_id": session["uuid"],
                "turn_id": tid,
                "tool_name": tool_name,
                "args_hash": args_hash(args),
                "status": "pending",
                "created_at": now,
                "expires_at": datetime.fromtimestamp(
                    now.timestamp() + self.ttl_s, tz=UTC
                ),
            },
        )
        return _public(self.need("approvals", who, id=rid))

    def get(self, owner_id: str, approval_id: int) -> dict[str, Any]:
        who = user_hex(owner_id)
        row = self.need("approvals", who, id=int(approval_id))
        if row["status"] == "pending" and self.now() >= row["expires_at"]:
            self.store.update("approvals", who, row["id"], {"status": "expired"})
            row = self.need("approvals", who, id=int(approval_id))
        return _public(row)

    def transition(self, owner_id: str, approval_id: int, to: str) -> dict[str, Any]:
        who = user_hex(owner_id)
        check_choice(to, APPROVAL_STATUSES, "approval_status")
        row = self.need("approvals", who, id=int(approval_id))
        lapsed = row["status"] == "pending" and self.now() >= row["expires_at"]
        status = "expired" if lapsed else row["status"]
        if to not in APPROVAL_TRANSITIONS[status] and not (lapsed and to == "expired"):
            raise PolicyDenied("approval_state")
        self.store.update("approvals", who, row["id"], {"status": to})
        return _public(self.need("approvals", who, id=int(approval_id)))


class MemoryTraces(_Base):
    def record(
        self,
        owner_id: str,
        turn_id: str,
        node: str,
        *,
        duration_ms: float = 0.0,
        status: str = "ok",
        error_code: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        who = user_hex(owner_id)
        turn = self.need("turns", who, uuid=uid(turn_id))
        clean = check_trace_detail(dict(detail or {}))
        rid = self.store.insert(
            "traces",
            who,
            {
                "turn_id": turn["uuid"],
                "ts": self.now(),
                "node": node,
                "duration_ms": duration_ms,
                "status": status,
                "error_code": error_code,
                "detail_json": copy.deepcopy(clean),
            },
        )
        return _public(self.need("traces", who, id=rid))

    def for_turn(self, owner_id: str, turn_id: str) -> list[dict[str, Any]]:
        who = user_hex(owner_id)
        turn = self.need("turns", who, uuid=uid(turn_id))
        rows = [
            r for r in self.store.select("traces", who) if r["turn_id"] == turn["uuid"]
        ]
        return [_public(r) for r in sorted(rows, key=lambda r: r["id"])]


class MemoryRateLimits(_Base):
    def hit(
        self, scope: str, key: str, *, window_s: int, now: float | None = None
    ) -> int:
        return self.store.hit(scope, key, window_s=window_s, now=self._t(now))

    def count(
        self, scope: str, key: str, *, window_s: int, now: float | None = None
    ) -> int:
        return self.store.peek(scope, key, window_s=window_s, now=self._t(now))

    def reset(self, scope: str, key: str) -> None:
        self.store.reset_limit(scope, key)

    def prune(self, older_than_s: float = 86400.0, now: float | None = None) -> int:
        return self.store.prune_limits(older_than_s, self._t(now))

    def _t(self, now: float | None) -> float:
        return self.clock() if now is None else now


class MemoryFieldRequests(_Base):
    def add(
        self,
        owner_id: str,
        field_id: str,
        *,
        note: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        who = user_hex(owner_id)
        sid = self.session(who, session_id)["uuid"] if session_id is not None else None
        MemoryCustomers(self.store, self.clock).ensure(who)
        rid = self.store.insert(
            "field_requests",
            who,
            {
                "session_id": sid,
                "field_id": field_id,
                "note": note,
                "status": "open",
                "created_at": self.now(),
            },
        )
        return self._out(self.need("field_requests", who, id=rid), who)

    @staticmethod
    def _out(row: dict[str, Any], who: str) -> dict[str, Any]:
        return {"user_id": who, **_public(row)}

    def history(self, owner_id: str) -> list[dict[str, Any]]:
        who = user_hex(owner_id)
        return [
            self._out(r, who)
            for r in sorted(
                self.store.select("field_requests", who), key=lambda r: r["id"]
            )
        ]


class MemoryDeletionRequests(_Base):
    def add(self, owner_id: str, reason: str | None = None) -> dict[str, Any]:
        who = user_hex(owner_id)
        MemoryCustomers(self.store, self.clock).ensure(who)
        rid = self.store.insert(
            "deletion_requests",
            who,
            {
                "reason": reason,
                "status": "open",
                "created_at": self.now(),
                "resolved_at": None,
            },
        )
        return self._out(self.need("deletion_requests", who, id=rid), who)

    @staticmethod
    def _out(row: dict[str, Any], who: str) -> dict[str, Any]:
        return {"user_id": who, **_public(row)}

    def history(self, owner_id: str) -> list[dict[str, Any]]:
        who = user_hex(owner_id)
        return [
            self._out(r, who)
            for r in sorted(
                self.store.select("deletion_requests", who), key=lambda r: r["id"]
            )
        ]


class MemoryAudit(_Base):
    def record(
        self,
        actor_user_id: str | None,
        action: str,
        target: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor = user_hex(actor_user_id) if actor_user_id else None
        clean = check_trace_detail(dict(detail or {}))
        rid = self.store.insert(
            "audit_log",
            SYSTEM_OWNER,
            {
                "actor_user_id": actor,
                "action": action,
                "target": target,
                "detail_json": copy.deepcopy(clean),
                "at": self.now(),
            },
        )
        return _public(self.need("audit_log", SYSTEM_OWNER, id=rid))

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = sorted(
            self.store.select("audit_log", SYSTEM_OWNER), key=lambda r: -r["id"]
        )
        return [_public(r) for r in rows[: max(1, min(limit, 1000))]]


class MemoryOperator(_Base):
    def delete_customer(self, actor_user_id: str, customer_id: str) -> None:
        actor = user_hex(actor_user_id)
        target = user_hex(customer_id)
        if self.find("customers", target) is None:
            raise NotFound()
        self.store.purge_owner(target)
        MemoryAudit(self.store, self.clock).record(actor, "customer_deleted", target)
