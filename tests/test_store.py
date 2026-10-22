"""Repositories: behaviour in memory, mirror of the Postgres classes, `Database` wiring, import hygiene.

Offline. No Postgres is opened: the Postgres repositories run against a fake pool that records statements, which
checks the connection protocol (the RLS identity is set first) and that every statement binds exactly its
parameters. The verifier's RLS run (`tests/test_store_identity.py --postgres`) covers the SQL itself.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from milo.errors import InvalidInput, NotFound, PolicyDenied
from milo.store import Repositories, memory_repositories, pg_repositories
from milo.store.common import Conflict, TurnRunning, check_trace_detail, uid, user_hex
from milo.store.db import (
    Database,
    check_role,
    norm_row,
    role_from_url,
    translate_error,
)
from milo.store.memory import MemoryStore

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def repos(clock: Clock) -> Repositories:
    return memory_repositories(MemoryStore(), clock)


def owner(repos: Repositories) -> str:
    who = uuid.uuid4().hex
    repos.profiles.create_confirmed(who, f"{who[:6]}@example.test")
    return who


# ---- profiles, customers, consents ----


def test_profiles_customers_consents(repos: Repositories) -> None:
    a = owner(repos)
    assert (
        repos.profiles.get(a)["role"] == "owner" and repos.profiles.role(a) == "owner"
    )
    repos.profiles.touch_last_seen(a)
    assert repos.profiles.get(a)["last_seen_at"] is not None
    assert repos.customers.get(a) is None
    assert (
        repos.customers.update(a, company_name="Acme Demo Ltd", locations=2)[
            "locations"
        ]
        == 2
    )
    assert repos.customers.ensure(a)["company_name"] == "Acme Demo Ltd"
    with pytest.raises(InvalidInput):
        repos.customers.update(a, role="operator")
    assert not repos.consents.has(a)
    repos.consents.record(a, "interview", "v0")
    assert repos.consents.has(a) and not repos.consents.has(a, "aggregate")
    assert [c["version"] for c in repos.consents.history(a)] == ["v0"]
    with pytest.raises(InvalidInput):
        repos.consents.record(a, "marketing", "v0")
    with pytest.raises(
        NotFound
    ):  # no profile yet: the trigger of D89 has not run for this account
        repos.customers.ensure(uuid.uuid4().hex)


def test_the_app_has_no_way_to_create_an_operator(repos: Repositories) -> None:
    a = owner(repos)
    assert not [
        m
        for m in dir(repos.profiles)
        if "role" in m and "set" in m and not m.endswith("_for_test")
    ]
    assert repos.profiles.role(a) == "owner"
    repos.profiles.set_role_for_test(a, "operator")
    assert repos.profiles.role(a) == "operator"


# ---- sessions (SPEC D97 to D102) ----


def test_sessions_lifecycle_and_search(repos: Repositories, clock: Clock) -> None:
    a = owner(repos)
    s1 = repos.sessions.create(a, "Bookings and invoices")
    clock.t += 10
    s2 = repos.sessions.create(a)
    clock.t += 10
    assert [s["id"] for s in repos.sessions.search(a)] == [s2["id"], s1["id"]], (
        "newest first"
    )
    assert [s["id"] for s in repos.sessions.search(a, query="invoice")] == [s1["id"]]
    renamed = repos.sessions.rename(a, s2["id"], "  Dispatch   routes ")
    assert renamed["title"] == "Dispatch routes"
    with pytest.raises(InvalidInput):
        repos.sessions.rename(a, s2["id"], "x" * 500)
    arch = repos.sessions.archive(a, s1["id"])
    assert arch["status"] == "archived" and arch["archived_at"] is not None
    assert repos.sessions.archive(a, s1["id"])["status"] == "archived"
    assert [s["id"] for s in repos.sessions.search(a)] == [s2["id"]]
    assert [s["id"] for s in repos.sessions.search(a, status="archived")] == [s1["id"]]
    back = repos.sessions.restore(a, s1["id"])
    assert back["status"] == "active" and back["archived_at"] is None
    repos.submissions.create(a, s1["id"], {"k": 1})
    repos.sessions.archive(a, s1["id"])
    assert repos.sessions.restore(a, s1["id"])["status"] == "submitted"
    assert len(repos.sessions.search(a)) == 2, "many sessions per account (D97)"
    assert (
        len(s1["id"]) == 32
        and repos.sessions.get(a, str(uuid.UUID(s1["id"])))["id"] == s1["id"]
    )
    with pytest.raises(NotFound):
        repos.sessions.get(a, "not-a-uuid")
    with pytest.raises(InvalidInput):
        repos.sessions.search(a, status="deleted")


def test_one_running_turn_per_account(repos: Repositories, clock: Clock) -> None:
    a = owner(repos)
    s1, s2 = repos.sessions.create(a), repos.sessions.create(a)
    t1 = repos.turns.start(a, s1["id"])
    assert t1["status"] == "running" and len(t1["id"]) == 32
    with pytest.raises(TurnRunning):  # per account, not per session (D131)
        repos.turns.start(a, s2["id"])
    repos.turns.add_event(a, t1["id"], "token", {"n": 1})
    repos.turns.add_event(a, t1["id"], "done", {"n": 2})
    assert [e["seq"] for e in repos.turns.events(a, t1["id"])] == [1, 2]
    assert repos.turns.finish(a, t1["id"], "done")["status"] == "done"
    assert repos.turns.finish(a, t1["id"], "error")["status"] == "done", (
        "finish is once"
    )
    t2 = repos.turns.start(a, s2["id"])
    clock.t += 301  # a crashed turn does not block the account for ever
    t3 = repos.turns.start(a, s1["id"])
    assert (
        repos.turns.get(a, t2["id"])["status"] == "error" and t3["status"] == "running"
    )
    repos.sessions.archive(a, s2["id"])
    repos.turns.finish(a, t3["id"])
    with pytest.raises(PolicyDenied):
        repos.turns.start(a, s2["id"])


# ---- messages, facts, evidence ----


def test_messages_facts_and_history(repos: Repositories, clock: Clock) -> None:
    a = owner(repos)
    s = repos.sessions.create(a)["id"]
    m1 = repos.messages.add(a, s, "owner", "We take about 40 bookings a day.", "h1")
    repos.messages.add(a, s, "assistant", "How are they received?")
    assert [m["id"] for m in repos.messages.history(a, s)][0] == m1["id"]
    assert len(repos.messages.history(a, s, after_id=m1["id"])) == 1
    assert len(repos.messages.history(a, s, limit=1)) == 1
    assert repos.messages.get(a, s, m1["id"])["trace_hash"] == "h1"
    with pytest.raises(InvalidInput):
        repos.messages.add(a, s, "owner", "   ")
    with pytest.raises(InvalidInput):
        repos.messages.add(a, s, "bot", "hi")
    f1 = repos.facts.upsert(
        a,
        s,
        "daily_request_volume",
        value=40,
        unit="requests",
        period="day",
        status="reported",
        source_message_id=m1["id"],
    )
    clock.t += 5
    f2 = repos.facts.upsert(a, s, "daily_request_volume", value=45, status="confirmed")
    assert (
        f1["value_json"] == 40
        and f2["value_json"] == 45
        and f2["status"] == "confirmed"
    )
    assert [
        h["value_json"] for h in repos.facts.field_history(a, s, "daily_request_volume")
    ] == [40, 45]
    assert len(repos.facts.for_session(a, s)) == 1
    with pytest.raises(InvalidInput):
        repos.facts.upsert(a, s, "x", value=1, status="maybe")
    other = repos.sessions.create(a)["id"]
    with pytest.raises(
        NotFound
    ):  # a message of another session cannot be cited as a source
        repos.facts.upsert(
            a, other, "y", value=1, status="reported", source_message_id=m1["id"]
        )
    ev = repos.evidence.add(
        a,
        s,
        "customer_statement",
        ref="m1",
        score=0.9,
        text_hash="abc",
        message_id=m1["id"],
    )
    assert repos.evidence.for_session(a, s) == [ev]
    with pytest.raises(InvalidInput):
        repos.evidence.add(a, s, "rumour")


# ---- submissions (SPEC D41) ----


def test_submissions_are_revisions_and_the_report_is_filled_once(
    repos: Repositories,
) -> None:
    a = owner(repos)
    s = repos.sessions.create(a)["id"]
    r1 = repos.submissions.create(a, s, {"facts": {"x": 1}})
    r2 = repos.submissions.create(a, s, {"facts": {"x": 2}})
    assert (r1["revision"], r2["revision"]) == (1, 2) and r1["status"] == "received"
    assert repos.sessions.get(a, s)["status"] == "submitted"
    done = repos.submissions.attach_report(
        a, r1["id"], report_json={"total": 3}, cost_base=10.0, currency="USD"
    )
    assert done["status"] == "report_ready" and done["report_json"] == {"total": 3}
    assert done["spec_snapshot_json"] == {"facts": {"x": 1}}
    with pytest.raises(PolicyDenied):
        repos.submissions.attach_report(a, r1["id"], report_json={"total": 4})
    assert (
        repos.submissions.set_status(a, r2["id"], "withdrawn")["status"] == "withdrawn"
    )
    assert repos.submissions.latest(a, s)["id"] == r2["id"]
    assert [x["revision"] for x in repos.submissions.for_session(a, s)] == [1, 2]
    with pytest.raises(InvalidInput):
        repos.submissions.set_status(a, r2["id"], "lost")


# ---- approvals (SPEC section 8, D48) ----


def test_approvals_lifecycle(repos: Repositories, clock: Clock) -> None:
    a = owner(repos)
    s = repos.sessions.create(a)["id"]
    t = repos.turns.start(a, s)["id"]
    ap = repos.approvals.create(a, s, "delete_item", {"item_id": "42"}, turn_id=t)
    assert ap["status"] == "pending" and len(ap["args_hash"]) == 16
    assert repos.approvals.transition(a, ap["id"], "used")["status"] == "used"
    with pytest.raises(PolicyDenied):
        repos.approvals.transition(
            a, ap["id"], "used"
        )  # an approval runs the tool once
    kept = repos.approvals.create(a, s, "delete_item", {"item_id": "7"})
    assert repos.approvals.transition(a, kept["id"], "kept")["status"] == "kept"
    old = repos.approvals.create(a, s, "delete_item", {"item_id": "9"})
    clock.t += 601
    assert repos.approvals.get(a, old["id"])["status"] == "expired"
    with pytest.raises(PolicyDenied):
        repos.approvals.transition(a, old["id"], "approved")
    late = repos.approvals.create(a, s, "delete_item", {"item_id": "5"})
    clock.t += 601
    with pytest.raises(PolicyDenied):
        repos.approvals.transition(a, late["id"], "used")
    with pytest.raises(NotFound):
        repos.approvals.create(a, s, "delete_item", {}, turn_id=uuid.uuid4().hex)


# ---- traces (SPEC D141) ----


def test_traces_hold_no_owner_text(repos: Repositories) -> None:
    a = owner(repos)
    s = repos.sessions.create(a)["id"]
    t = repos.turns.start(a, s)["id"]
    ok = repos.traces.record(
        a,
        t,
        "model_call",
        duration_ms=12.5,
        detail={
            "model": "m/x",
            "tokens_in": 20,
            "args_hash": "ab12",
            "length": 55,
            "tools": ["lookup_item"],
        },
    )
    assert repos.traces.for_turn(a, t) == [ok]
    for bad in (
        {"text": "hello"},
        {"nested": {"evidence_text": "x"}},
        {"note": "word " * 40},
        {"calls": [{"arguments": {}}]},
    ):
        with pytest.raises(InvalidInput):
            repos.traces.record(a, t, "x", detail=bad)
        with pytest.raises(InvalidInput):
            check_trace_detail(bad)
    assert len(repos.traces.for_turn(a, t)) == 1


# ---- rate limits (SPEC D142), requests, audit, operator ----


def test_rate_limit_counters(repos: Repositories) -> None:
    rl = repos.rate_limits
    assert rl.hit("login", "k", window_s=60, now=10.0) == 1
    assert rl.hit("login", "k", window_s=60, now=20.0) == 2
    assert rl.count("login", "k", window_s=60, now=30.0) == 2
    assert rl.hit("login", "k", window_s=60, now=70.0) == 1, (
        "a new window starts at zero"
    )
    assert rl.count("login", "other", window_s=60, now=30.0) == 0
    rl.reset("login", "k")
    assert rl.count("login", "k", window_s=60, now=70.0) == 0
    rl.hit("x", "k", window_s=60, now=0.0)
    assert rl.prune(older_than_s=100.0, now=1000.0) == 1


def test_owner_requests_audit_and_operator_delete(repos: Repositories) -> None:
    a, b = owner(repos), owner(repos)
    s = repos.sessions.create(a)["id"]
    repos.messages.add(a, s, "owner", "hello there")
    repos.consents.record(a, "interview", "v0")
    repos.field_requests.add(
        a, "daily_request_volume", note="please ask again", session_id=s
    )
    repos.deletion_requests.add(a, "closing the business")
    assert (
        len(repos.field_requests.history(a)) == 1
        and len(repos.deletion_requests.history(a)) == 1
    )
    assert (
        repos.field_requests.history(b) == []
        and repos.deletion_requests.history(b) == []
    )
    repos.audit.record(a, "login", detail={"ok": True})
    assert repos.audit.recent()[0]["action"] == "login"
    with pytest.raises(InvalidInput):
        repos.audit.record(a, "x", detail={"text": "owner words"})
    repos.operator.delete_customer(
        b, a
    )  # an operator deletes a customer; cascade plus audit row
    assert repos.sessions.search(a) == [] and repos.customers.get(a) is None
    assert repos.profiles.get(a)["role"] == "owner", "the account itself stays"
    assert repos.audit.recent()[0]["action"] == "customer_deleted"
    with pytest.raises(NotFound):
        repos.operator.delete_customer(b, a)


def test_unusable_owner_ids_are_refused(repos: Repositories) -> None:
    with pytest.raises(PolicyDenied):
        repos.sessions.create("owner-a")
    with pytest.raises(PolicyDenied):
        user_hex("")
    with pytest.raises(NotFound):
        uid("42")


# ---- the memory classes mirror the Postgres classes ----

MEMORY_ONLY = {
    "MemoryProfiles": {"create_confirmed", "set_role_for_test"},
}


def _public(cls: type) -> dict[str, inspect.Signature]:
    return {
        n: inspect.signature(f)
        for n, f in inspect.getmembers(cls, inspect.isfunction)
        if not n.startswith("_")
    }


def _pairs() -> list[tuple[str, type, type]]:
    fake = Database("p" + "ostgresql://milo_app.x@h/db", pool=object())
    pg = pg_repositories(fake)
    mem = memory_repositories(MemoryStore())
    return [
        (name, type(getattr(mem, name)), type(getattr(pg, name)))
        for name in Repositories.__dataclass_fields__
    ]


@pytest.mark.parametrize("name,mem_cls,pg_cls", _pairs())
def test_memory_repository_mirrors_postgres(
    name: str, mem_cls: type, pg_cls: type
) -> None:
    mem, pg = _public(mem_cls), _public(pg_cls)
    mem_only = MEMORY_ONLY.get(mem_cls.__name__, set())
    for method in {
        m for m in mem if m not in mem_only and m not in _public(object.__class__)
    }:
        if method in (
            "now",
            "find",
            "need",
            "session",
        ):  # helpers of the memory base class
            continue
        assert method in pg, f"{name}.{method} is missing from {pg_cls.__name__}"
        a, b = (
            list(mem[method].parameters.values()),
            list(pg[method].parameters.values()),
        )
        assert [(p.name, p.kind, p.default) for p in a[1:]] == [
            (p.name, p.kind, p.default) for p in b[1:]
        ], f"{name}.{method} signature differs"
    extra = {m for m in pg if m not in mem}
    assert not extra, f"{pg_cls.__name__} has methods that memory lacks: {extra}"


# ---- Database wiring (SPEC D74, D75) ----


class FakeCursor:
    rowcount = 1

    def __init__(self, row: dict[str, Any], sql: str = "") -> None:
        self.row, self.sql = row, sql

    def fetchone(self) -> dict[str, Any] | None:
        if "status = 'running' limit 1" in self.sql:  # no other running turn
            return None
        return dict(self.row)

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(self.row)]


class FakeConn:
    def __init__(
        self, log: list[tuple[str, tuple[Any, ...]]], row: dict[str, Any]
    ) -> None:
        self.log, self.row = log, row

    def execute(self, sql: str, params: Any = ()) -> FakeCursor:
        self.log.append((sql, tuple(params)))
        return FakeCursor(self.row, sql)


class FakePool:
    def __init__(self) -> None:
        self.log: list[tuple[str, tuple[Any, ...]]] = []
        self.opened = 0
        self.row: dict[str, Any] = {
            "id": 1,
            "status": "pending",
            "past": False,
            "count": 1,
            "role": "owner",
            "session_id": "a" * 32,
            "turn_id": "b" * 32,
            "seq": 1,
            "revision": 1,
            "report_json": None,
            "email": "a@example.test",
        }

    @contextmanager
    def connection(self) -> Any:
        self.opened += 1
        yield FakeConn(self.log, self.row)


APP_URL = "p" + "ostgresql://milo_app.projectref@pooler.example.test:5432/db"
OWNER = uuid.uuid4().hex


def test_role_checks_never_echo_the_url_and_refuse_admin_roles() -> None:
    assert role_from_url(APP_URL) == "milo_app" and check_role(APP_URL) == "milo_app"
    for bad in (
        "p" + "ostgresql://postgres.ref@h/db",
        "p" + "ostgresql://service_role.ref@h/db",
        "p" + "ostgresql://h/db",
    ):
        with pytest.raises(RuntimeError) as info:
            check_role(bad)
        assert bad not in str(info.value) and "projectref" not in str(info.value)
    with pytest.raises(ValueError):
        Database(APP_URL, pool=object(), schema="x; drop table y")


def test_transaction_sets_the_rls_identity_first_and_only_with_an_owner() -> None:
    pool = FakePool()
    db = Database(APP_URL, pool=pool)
    db.one(OWNER, "select 1")
    sql, params = pool.log[0]
    assert "set_config('milo.user_id'" in sql and sql.endswith("true)"), (
        "transaction-local setting"
    )
    assert params == (str(uuid.UUID(OWNER)),) and pool.log[1][0] == "select 1"
    pool.log.clear()
    db.run(None, "delete from rate_limits")
    assert [s for s, _ in pool.log] == ["delete from rate_limits"]
    with pytest.raises(PolicyDenied):
        db.one("'; drop table sessions; --", "select 1")
    assert pool.opened == 2, "a bad owner id is refused before a connection is taken"


def test_error_translation_by_sqlstate() -> None:
    class Err(Exception):
        def __init__(self, state: str) -> None:
            self.sqlstate = state

    assert isinstance(translate_error(Err("42501")), NotFound)
    assert isinstance(translate_error(Err("23503")), NotFound)
    assert isinstance(translate_error(Err("23505")), Conflict)
    other = Err("40001")
    assert translate_error(other) is other

    pool = FakePool()
    db = Database(APP_URL, pool=pool)
    with pytest.raises(NotFound):
        with db.transaction(OWNER):
            raise Err("42501")


def test_norm_row_makes_hex_ids_and_floats() -> None:
    from decimal import Decimal

    u = uuid.uuid4()
    assert norm_row({"id": u, "n": Decimal("1.5"), "s": "x"}) == {
        "id": u.hex,
        "n": 1.5,
        "s": "x",
    }
    assert norm_row(None) is None


# ---- Postgres repositories against the fake pool: protocol and parameter binding ----

SID, TID = "a" * 32, "b" * 32


def _calls(r: Repositories) -> list[tuple[str, str, tuple[Any, ...], dict[str, Any]]]:
    o = OWNER
    return [
        ("profiles", "get", (o,), {}),
        ("profiles", "role", (o,), {}),
        ("profiles", "touch_last_seen", (o,), {}),
        ("customers", "get", (o,), {}),
        ("customers", "ensure", (o,), {}),
        ("customers", "update", (o,), {"company_name": "Acme", "locations": 2}),
        ("consents", "record", (o, "interview", "v0"), {}),
        ("consents", "has", (o,), {}),
        ("consents", "history", (o,), {}),
        ("sessions", "create", (o, "t"), {}),
        ("sessions", "get", (o, SID), {}),
        ("sessions", "search", (o,), {"query": "a_b%", "status": "active"}),
        ("sessions", "search", (o,), {}),
        ("sessions", "rename", (o, SID, "x"), {}),
        ("sessions", "archive", (o, SID), {}),
        ("sessions", "restore", (o, SID), {}),
        ("sessions", "set_status", (o, SID, "submitted"), {}),
        ("sessions", "touch", (o, SID), {}),
        ("turns", "start", (o, SID), {}),
        ("turns", "get", (o, TID), {}),
        ("turns", "finish", (o, TID, "done"), {}),
        ("turns", "add_event", (o, TID, "token", {"a": 1}), {}),
        ("turns", "events", (o, TID), {}),
        ("messages", "add", (o, SID, "owner", "hi", "h"), {}),
        ("messages", "get", (o, SID, 1), {}),
        ("messages", "history", (o, SID), {"after_id": 1, "limit": 5}),
        (
            "facts",
            "upsert",
            (o, SID, "f"),
            {"value": {"v": 1}, "status": "reported", "source_message_id": 1},
        ),
        ("facts", "get", (o, SID, "f"), {}),
        ("facts", "for_session", (o, SID), {}),
        ("facts", "field_history", (o, SID, "f"), {}),
        (
            "evidence",
            "add",
            (o, SID, "kb_chunk"),
            {"ref": "r", "score": 0.5, "message_id": 1},
        ),
        ("evidence", "for_session", (o, SID), {}),
        ("submissions", "create", (o, SID, {"k": 1}), {}),
        ("submissions", "get", (o, 1), {}),
        ("submissions", "for_session", (o, SID), {}),
        ("submissions", "latest", (o, SID), {}),
        (
            "submissions",
            "attach_report",
            (o, 1),
            {"report_json": {"a": 1}, "cost_base": 1.0},
        ),
        ("submissions", "set_status", (o, 1, "failed"), {}),
        ("approvals", "create", (o, SID, "delete_item", {"i": 1}), {"turn_id": TID}),
        ("approvals", "get", (o, 1), {}),
        ("approvals", "transition", (o, 1, "approved"), {}),
        ("traces", "record", (o, TID, "node"), {"detail": {"a": 1}}),
        ("traces", "for_turn", (o, TID), {}),
        ("rate_limits", "hit", ("s", "k"), {"window_s": 60}),
        ("rate_limits", "count", ("s", "k"), {"window_s": 60}),
        ("rate_limits", "reset", ("s", "k"), {}),
        ("rate_limits", "prune", (), {}),
        ("field_requests", "add", (o, "f"), {"note": "n", "session_id": SID}),
        ("field_requests", "history", (o,), {}),
        ("deletion_requests", "add", (o, "why"), {}),
        ("deletion_requests", "history", (o,), {}),
        ("audit", "record", (o, "login", "t", {"ok": True}), {}),
        ("audit", "recent", (), {}),
        ("operator", "delete_customer", (o, o), {}),
    ]


def test_every_postgres_method_runs_and_binds_its_parameters() -> None:
    pool = FakePool()
    repos = pg_repositories(Database(APP_URL, pool=pool))
    seen: set[tuple[str, str]] = set()
    for repo, method, args, kwargs in _calls(repos):
        pool.log.clear()
        getattr(getattr(repos, repo), method)(*args, **kwargs)
        seen.add((repo, method))
        assert pool.log, f"{repo}.{method} ran no statement"
        for sql, params in pool.log:
            assert sql.count("%s") == len(params), f"{repo}.{method}: {sql!r}"
            assert "%(" not in sql
        owner_scoped = repo not in ("rate_limits", "operator", "audit")
        if owner_scoped:
            assert "set_config('milo.user_id'" in pool.log[0][0], (
                f"{repo}.{method} must set the identity first"
            )
            assert pool.log[0][1] == (str(uuid.UUID(OWNER)),)
        else:
            assert not any("set_config" in s for s, _ in pool.log)
    for name in Repositories.__dataclass_fields__:
        for method in _public(type(getattr(repos, name))):
            assert (name, method) in seen, f"{name}.{method} is not exercised"


def test_session_search_escapes_like_wildcards() -> None:
    pool = FakePool()
    repos = pg_repositories(Database(APP_URL, pool=pool))
    repos.sessions.search(OWNER, query="50%_off")
    assert pool.log[-1][1][1] == "%50\\%\\_off%"


def test_postgres_repositories_use_the_row_filter_of_rls_not_a_caller_supplied_owner() -> (
    None
):
    """No statement takes the owner from anywhere but the transaction setting, except the explicit user_id columns."""
    for path in sorted((ROOT / "src/milo/store").glob("*.py")):
        text = path.read_text()
        assert "service_role" not in text and "auth.uid()" not in text, path.name


# ---- import hygiene (SPEC 11.12, D75) ----


def _loaded_after(code: str) -> list[str]:
    done = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": ""},
    )
    assert done.returncode == 0, done.stderr
    return eval(done.stdout.strip().splitlines()[-1])


def test_import_milo_app_loads_no_database_driver_http_client_or_auth_code() -> None:
    bad = _loaded_after(
        "import sys, milo.app; "
        "print([m for m in sys.modules if m.split('.')[0] in ('psycopg', 'psycopg_pool', 'psycopg_binary', 'httpx', 'jwt') "
        "or m.startswith(('milo.auth', 'milo.store.db'))])"
    )
    assert bad == []


def test_importing_the_store_and_building_repositories_opens_no_driver() -> None:
    bad = _loaded_after(
        "import sys; from milo.store import memory_repositories, pg_repositories; from milo.store.db import Database; "
        "memory_repositories(); "
        "print([m for m in sys.modules if m.split('.')[0] in ('psycopg', 'psycopg_pool')])"
    )
    assert bad == []


# ---- administrative connection, migrate and reset (SPEC D153 to D155) ----

ADMIN_URL = "p" + "ostgresql://postgres.projectref@pooler.example.test:5432/db"


def test_set_role_runs_first_and_is_transaction_local() -> None:
    pool = FakePool()
    db = Database(ADMIN_URL, pool=pool, set_role="milo_app")
    db.one(OWNER, "select 1")
    assert [s for s, _ in pool.log[:2]] == [
        "set local role milo_app",
        "select set_config('milo.user_id', %s, true)",
    ]
    with pytest.raises(ValueError):
        Database(ADMIN_URL, pool=pool, set_role="postgres")
    with pytest.raises(RuntimeError):  # an admin URL is refused unless asked for
        Database(ADMIN_URL, pool=pool)
    assert Database(ADMIN_URL, pool=pool, admin=True).role == "admin"


def test_migrate_refuses_prod_without_the_owner_flag() -> None:
    from milo.store.migrate import check_target

    check_target({})
    check_target({"MILO_ENV": "test"})
    with pytest.raises(RuntimeError):
        check_target({"MILO_ENV": "prod"})
    check_target({"MILO_ENV": "prod", "MILO_MIGRATE_PROD": "1"})


def test_reset_covers_every_migration_object_and_refuses_prod() -> None:
    import re

    from milo.store import reset

    sql = (ROOT / "db/migrations/0001_init.sql").read_text()
    created = set(re.findall(r"^create table (\w+) \(", sql, re.MULTILINE))
    assert created <= set(reset.tables())
    stmts = reset.statements()
    assert stmts[0] == "drop trigger if exists on_auth_user_confirmed on auth.users"
    for fn in re.findall(r"create or replace function ([\w.]+\([^)]*\))", sql):
        name = fn if "." in fn else "public." + fn
        assert any(name.split("(")[0] in s for s in stmts), fn
    assert any("drop role milo_app" in s for s in stmts) and any(
        "drop role milo_operator" in s for s in stmts
    )
    assert all("prod" not in s for s in stmts)
    with pytest.raises(RuntimeError):
        reset.refuse_prod({"MILO_ENV": "prod", "MILO_MIGRATE_PROD": "1"})
    reset.refuse_prod({"MILO_ENV": "test"})


def test_migration_is_idempotent_in_form() -> None:
    import re

    sql = (ROOT / "db/migrations/0001_init.sql").read_text()
    assert not re.search(r"^create function", sql, re.MULTILINE)
    assert "search_path" not in sql
    assert "drop trigger if exists on_auth_user_confirmed on auth.users;" in sql
    assert "drop trigger if exists submissions_immutable_trg on submissions;" in sql
    assert (
        "grant %I to current_user" in sql
        and "grant milo_app, milo_operator to current_user" in sql
    )
    assert sql.count("$$") % 2 == 0
    assert sql.count("(") == sql.count(")")
