"""The owner filter on every repository call (SPEC D74, D91, D129; HARNESS 7 item 8).

Every owner-facing call that carries another owner's ids must fail with `not_found` and change nothing; every
call keyed only by the caller's own id must show nothing of another owner's data. The suite runs two ways:

* in pytest, against the in-memory repositories (offline);
* by the verifier against Postgres, outside pytest (SPEC D153, D154), with row-level security doing the filtering:

      make migrate && uv run python tests/test_store_identity.py --postgres && make db-reset

  It opens the administrative connection, creates two confirmed test accounts, and runs every check under
  `SET LOCAL ROLE milo_app`. No role password is used. It refuses production settings (D76), prints PASS or FAIL
  per call, then the table probes (RLS and FORCE flags and a policy on every table read from `db/migrations`;
  no identity, another owner, `profiles.role`, `audit_log`), and deletes its two accounts at the end.
"""

from __future__ import annotations

import argparse
import inspect
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from milo.errors import NotFound  # noqa: E402
from milo.store import Repositories, memory_repositories  # noqa: E402
from milo.store.memory import MemoryStore  # noqa: E402

Ids = dict[str, Any]
Call = Callable[[Repositories, str, Ids], Any]


def seed(r: Repositories, a: str) -> Ids:
    """Owner A's data in every owner table."""
    r.customers.update(a, company_name="Alpha Demo Ltd")
    r.consents.record(a, "interview", "v0")
    sid = r.sessions.create(a, "Alpha session")["id"]
    archived = r.sessions.create(a, "Alpha archived")["id"]
    r.sessions.archive(a, archived)
    mid = r.messages.add(a, sid, "owner", "alpha says hello", "hash-a")["id"]
    r.facts.upsert(
        a,
        sid,
        "daily_request_volume",
        value=40,
        status="reported",
        source_message_id=mid,
    )
    r.evidence.add(a, sid, "customer_statement", ref="m", message_id=mid)
    sub = r.submissions.create(a, sid, {"alpha": True})["id"]
    tid = r.turns.start(a, sid)["id"]
    r.turns.add_event(a, tid, "token", {"t": "x"})
    r.traces.record(a, tid, "model_call", detail={"model": "m/x"})
    # A day's expiry, so a slow run cannot lapse the approval between the seed and the checks.
    apid = r.approvals.create(
        a, sid, "delete_item", {"item_id": "1"}, turn_id=tid, ttl_s=86400.0
    )["id"]
    r.field_requests.add(a, "daily_request_volume", session_id=sid)
    r.deletion_requests.add(a, "alpha reason")
    return {
        "sid": sid,
        "archived": archived,
        "mid": mid,
        "sub": sub,
        "tid": tid,
        "apid": apid,
    }


def digest(r: Repositories, a: str, ids: Ids) -> dict[str, Any]:
    """Everything owner A can read through the repositories, for a before and after comparison."""
    sid, tid = ids["sid"], ids["tid"]
    return {
        "profile": r.profiles.get(a),
        "customer": r.customers.get(a),
        "consents": r.consents.history(a),
        "sessions": r.sessions.search(a),
        "archived": r.sessions.search(a, status="archived"),
        "messages": r.messages.history(a, sid),
        "facts": r.facts.for_session(a, sid),
        "fact_history": r.facts.field_history(a, sid, "daily_request_volume"),
        "evidence": r.evidence.for_session(a, sid),
        "submissions": r.submissions.for_session(a, sid),
        "turn": r.turns.get(a, tid),
        "events": r.turns.events(a, tid),
        "traces": r.traces.for_turn(a, tid),
        "approval": r.approvals.get(a, ids["apid"]),
        "field_requests": r.field_requests.history(a),
        "deletion_requests": r.deletion_requests.history(a),
    }


# Calls that carry A's ids, made by B. Each must raise NotFound.
NOT_FOUND: dict[str, Call] = {
    "sessions.get": lambda r, b, i: r.sessions.get(b, i["sid"]),
    "sessions.rename": lambda r, b, i: r.sessions.rename(b, i["sid"], "hijacked"),
    "sessions.archive": lambda r, b, i: r.sessions.archive(b, i["sid"]),
    "sessions.restore": lambda r, b, i: r.sessions.restore(b, i["archived"]),
    "sessions.set_status": lambda r, b, i: r.sessions.set_status(
        b, i["sid"], "archived"
    ),
    "sessions.touch": lambda r, b, i: r.sessions.touch(b, i["sid"]),
    "turns.start": lambda r, b, i: r.turns.start(b, i["sid"]),
    "turns.get": lambda r, b, i: r.turns.get(b, i["tid"]),
    "turns.finish": lambda r, b, i: r.turns.finish(b, i["tid"], "done"),
    "turns.add_event": lambda r, b, i: r.turns.add_event(b, i["tid"], "token", {}),
    "turns.events": lambda r, b, i: r.turns.events(b, i["tid"]),
    "messages.add": lambda r, b, i: r.messages.add(b, i["sid"], "owner", "injected"),
    "messages.get": lambda r, b, i: r.messages.get(b, i["sid"], i["mid"]),
    "messages.history": lambda r, b, i: r.messages.history(b, i["sid"]),
    "facts.upsert": lambda r, b, i: r.facts.upsert(
        b, i["sid"], "daily_request_volume", value=1, status="confirmed"
    ),
    "facts.get": lambda r, b, i: r.facts.get(b, i["sid"], "daily_request_volume"),
    "facts.for_session": lambda r, b, i: r.facts.for_session(b, i["sid"]),
    "facts.field_history": lambda r, b, i: r.facts.field_history(
        b, i["sid"], "daily_request_volume"
    ),
    "evidence.add": lambda r, b, i: r.evidence.add(b, i["sid"], "kb_chunk"),
    "evidence.for_session": lambda r, b, i: r.evidence.for_session(b, i["sid"]),
    "submissions.create": lambda r, b, i: r.submissions.create(b, i["sid"], {"x": 1}),
    "submissions.get": lambda r, b, i: r.submissions.get(b, i["sub"]),
    "submissions.for_session": lambda r, b, i: r.submissions.for_session(b, i["sid"]),
    "submissions.latest": lambda r, b, i: r.submissions.latest(b, i["sid"]),
    "submissions.attach_report": lambda r, b, i: r.submissions.attach_report(
        b, i["sub"], report_json={"x": 1}
    ),
    "submissions.set_status": lambda r, b, i: r.submissions.set_status(
        b, i["sub"], "withdrawn"
    ),
    "approvals.create": lambda r, b, i: r.approvals.create(
        b, i["sid"], "delete_item", {}
    ),
    "approvals.get": lambda r, b, i: r.approvals.get(b, i["apid"]),
    "approvals.transition": lambda r, b, i: r.approvals.transition(
        b, i["apid"], "approved"
    ),
    "traces.record": lambda r, b, i: r.traces.record(b, i["tid"], "node"),
    "traces.for_turn": lambda r, b, i: r.traces.for_turn(b, i["tid"]),
    "field_requests.add": lambda r, b, i: r.field_requests.add(
        b, "f", session_id=i["sid"]
    ),
}


def _mixed(r: Repositories, b: str, i: Ids, which: str) -> Any:
    """B owns a session of their own but names A's message, turn or session inside it."""
    mine = r.sessions.create(b, "Beta session")["id"]
    if which == "fact_source":
        return r.facts.upsert(
            b, mine, "f", value=1, status="reported", source_message_id=i["mid"]
        )
    if which == "evidence_message":
        return r.evidence.add(b, mine, "kb_chunk", message_id=i["mid"])
    if which == "approval_turn":
        return r.approvals.create(b, mine, "delete_item", {}, turn_id=i["tid"])
    raise AssertionError(which)


for _name in ("fact_source", "evidence_message", "approval_turn"):
    NOT_FOUND[f"mixed.{_name}"] = lambda r, b, i, _n=_name: _mixed(r, b, i, _n)

# Calls keyed only by the caller's own id: B sees nothing of A.
EMPTY: dict[str, Call] = {
    "sessions.search": lambda r, b, i: r.sessions.search(b),
    "sessions.search_archived": lambda r, b, i: r.sessions.search(b, status="archived"),
    "sessions.search_title": lambda r, b, i: r.sessions.search(b, query="Alpha"),
    "customers.get": lambda r, b, i: r.customers.get(b),
    "consents.has": lambda r, b, i: r.consents.has(b),
    "consents.history": lambda r, b, i: r.consents.history(b),
    "field_requests.history": lambda r, b, i: r.field_requests.history(b),
    "deletion_requests.history": lambda r, b, i: r.deletion_requests.history(b),
}

# Own-id writes and reads: they act on B's rows only; A's data must not move and nothing of A may appear.
OWN: dict[str, Call] = {
    "profiles.get": lambda r, b, i: r.profiles.get(b),
    "profiles.role": lambda r, b, i: r.profiles.role(b),
    "profiles.touch_last_seen": lambda r, b, i: r.profiles.touch_last_seen(b),
    "customers.ensure": lambda r, b, i: r.customers.ensure(b),
    "customers.update": lambda r, b, i: r.customers.update(
        b, company_name="Beta Demo Ltd"
    ),
    "consents.record": lambda r, b, i: r.consents.record(b, "interview", "v0"),
    "sessions.create": lambda r, b, i: r.sessions.create(b, "Beta"),
    "deletion_requests.add": lambda r, b, i: r.deletion_requests.add(b, "beta"),
    "field_requests.add_own": lambda r, b, i: r.field_requests.add(b, "f"),
}

# Not owner-facing: keyed by scope or by operator, never by an owner's id.
NOT_OWNER_FACING = {
    "rate_limits.hit", "rate_limits.count", "rate_limits.reset", "rate_limits.prune",
    "audit.record", "audit.recent", "operator.delete_customer",
    "operator.submissions", "operator.submission",
    "profiles.create_confirmed", "profiles.set_role_for_test",
}  # fmt: skip
HELPERS = {"now", "find", "need", "session"}


ALIASES = {
    "sessions.search_archived": "sessions.search",
    "sessions.search_title": "sessions.search",
    "field_requests.add_own": "field_requests.add",
}


def covered() -> set[str]:
    named = set(NOT_FOUND) | set(EMPTY) | set(OWN) | NOT_OWNER_FACING
    return {ALIASES.get(n, n) for n in named if not n.startswith("mixed.")}


def public_methods(r: Repositories) -> set[str]:
    out = set()
    for attr in Repositories.__dataclass_fields__:
        for name in dir(getattr(r, attr)):
            if (
                not name.startswith("_")
                and inspect.ismethod(getattr(getattr(r, attr), name))
                and name not in HELPERS
            ):
                out.add(f"{attr}.{name}")
    return out


def leaks(out: Any, a: str, ids: Ids) -> bool:
    """True when a result mentions owner A, A's string ids or A's seeded text."""
    text = repr(out)
    needles = [a, "Alpha", "alpha", *(v for v in ids.values() if isinstance(v, str))]
    return any(n in text for n in needles)


def check_one(r: Repositories, a: str, b: str, ids: Ids, name: str) -> str | None:
    """Run one call as B. Returns a failure text, or None when the call behaved."""
    before = digest(r, a, ids)
    if name in NOT_FOUND:
        try:
            NOT_FOUND[name](r, b, ids)
        except NotFound:
            pass
        except Exception as exc:  # any other error type is a different leak
            return f"{name}: raised {type(exc).__name__}, expected not_found"
        else:
            return f"{name}: succeeded with another owner's id"
    else:
        out = (EMPTY.get(name) or OWN[name])(r, b, ids)
        if leaks(out, a, ids):
            return f"{name}: the result shows the other owner's data"
    if digest(r, a, ids) != before:
        return f"{name}: changed the other owner's data"
    return None


def run_suite(r: Repositories, a: str, b: str) -> list[str]:
    ids = seed(r, a)
    return [
        f for name in [*NOT_FOUND, *EMPTY, *OWN] if (f := check_one(r, a, b, ids, name))
    ]


# ---- pytest, in memory ----


def memory_world() -> tuple[Repositories, str, str, Ids]:
    r = memory_repositories(MemoryStore())
    a, b = uuid.uuid4().hex, uuid.uuid4().hex
    r.profiles.create_confirmed(a, "alpha@example.test")
    r.profiles.create_confirmed(b, "beta@example.test")
    return r, a, b, seed(r, a)


@pytest.mark.parametrize("name", [*NOT_FOUND, *EMPTY, *OWN])
def test_cross_owner_call_is_refused_and_changes_nothing(name: str) -> None:
    r, a, b, ids = memory_world()
    snapshot = r.sessions.store.snapshot()
    failure = check_one(r, a, b, ids, name)
    assert failure is None, failure
    if name in NOT_FOUND and not name.startswith("mixed."):
        assert r.sessions.store.snapshot() == snapshot, "the in-memory tables changed"


def test_every_owner_facing_method_is_in_the_suite() -> None:
    r, *_ = memory_world()
    missing = public_methods(r) - covered()
    assert not missing, f"add these to test_store_identity.py: {sorted(missing)}"


def test_not_found_calls_do_not_say_whose_row_it_is() -> None:
    r, a, b, ids = memory_world()
    messages = set()
    for call in (NOT_FOUND["sessions.get"], NOT_FOUND["turns.get"]):
        with pytest.raises(NotFound) as foreign:
            call(r, b, ids)
        messages.add(foreign.value.error)
    with pytest.raises(NotFound) as missing:
        r.sessions.get(b, uuid.uuid4().hex)
    assert messages == {"not_found"} and missing.value.error == "not_found"


def test_the_owner_filter_holds_in_both_directions() -> None:
    for swap in (False, True):
        r = memory_repositories(MemoryStore())
        a, b = uuid.uuid4().hex, uuid.uuid4().hex
        r.profiles.create_confirmed(a)
        r.profiles.create_confirmed(b)
        first, second = (b, a) if swap else (a, b)
        assert run_suite(r, first, second) == []


def test_the_suite_detects_a_broken_owner_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from milo.store import memory
    from milo.store.common import uid

    def leaky(self: Any, owner: str, session_id: str) -> dict[str, Any]:
        for row in self.store._tables.get("sessions", {}).values():
            if row["uuid"] == uid(session_id):
                return dict(row)
        raise NotFound()

    monkeypatch.setattr(memory._Base, "session", leaky)
    r = memory_repositories(MemoryStore())
    a, b = uuid.uuid4().hex, uuid.uuid4().hex
    r.profiles.create_confirmed(a)
    r.profiles.create_confirmed(b)
    failures = run_suite(r, a, b)
    assert any(f.startswith("messages.history") for f in failures), failures


# ---- Postgres, run by the verifier outside pytest ----


def _state(exc: BaseException) -> str | None:
    return getattr(exc, "sqlstate", None)


PROBE_GROUPS = ("A-09c", "A-09a", "A-09b", "A-10b", "A-27", "counts")


def sql_probes(url: str, a: str, b: str) -> list[str]:
    """Table-level probes of the SR cases (A-09a, A-09c, A-10b, A-27, D140), as `milo_app` and `milo_operator`.

    Each probe runs in its own transaction that is rolled back. Returns failure texts; empty means all passed."""
    from milo.store.db import Database
    from milo.store.reset import tables

    fails: list[str] = []
    app = Database(url, set_role="milo_app")
    op = Database(url, set_role="milo_operator")
    app.open()
    op.open()

    def scalar(
        db: Database,
        role: str,
        owner: str | None,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> Any:
        with db.probe(role, owner) as conn:
            row = conn.execute(sql, params).fetchone()
            return next(iter(row.values())) if row else None

    def col(db: Database, sql: str, params: tuple[Any, ...] = ()) -> list[str]:
        with op.probe("milo_operator", None) as conn:
            return [
                str(next(iter(r.values())))
                for r in conn.execute(sql, params).fetchall()
            ]

    def expect_state(
        label: str,
        db: Database,
        role: str,
        owner: str | None,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> None:
        try:
            with db.probe(role, owner) as conn:
                conn.execute(sql, params)
        except Exception as exc:
            if _state(exc) != "42501":
                fails.append(
                    f"{label}: expected SQLSTATE 42501, got {_state(exc) or type(exc).__name__}"
                )
            return
        fails.append(f"{label}: succeeded, expected 42501")

    try:
        # D140 / A-09c: enumerate tables from the migrations; RLS on, forced (except schema_migrations), a policy.
        owner_tables = [t for t in tables() if t != "schema_migrations"]
        for t in tables():
            flags = scalar(
                op,
                "milo_operator",
                None,
                "select relrowsecurity::text || relforcerowsecurity::text from pg_class where oid = %s::regclass",
                (f"public.{t}",),
            )
            want = "truefalse" if t == "schema_migrations" else "truetrue"
            if flags != want:
                fails.append(
                    f"A-09c {t}: relrowsecurity/relforcerowsecurity = {flags}, expected {want}"
                )
            if not scalar(
                op,
                "milo_operator",
                None,
                "select count(*) from pg_policies where schemaname = 'public' and tablename = %s",
                (t,),
            ):
                fails.append(f"A-09c {t}: no policy")
        for role in ("milo_app", "milo_operator"):
            bad = scalar(
                op,
                "milo_operator",
                None,
                "select rolsuper or rolbypassrls or rolcreaterole from pg_roles where rolname = %s",
                (role,),
            )
            if bad:
                fails.append(f"A-09c {role}: superuser, bypassrls or createrole")

        # Counts of owner A's rows, read by the operator (SELECT everywhere), to prove nothing changes.
        def counts() -> dict[str, int]:
            return {
                t: int(scalar(op, "milo_operator", None, f"select count(*) from {t}"))
                for t in [*owner_tables, "profiles"]
            }

        before = counts()
        # A-09a: no milo.user_id set -> 0 rows, and an insert is refused.
        for t in owner_tables:
            if t == "audit_log":
                continue
            try:
                n = scalar(app, "milo_app", None, f"select count(*) from {t}")
            except Exception as exc:
                if _state(exc) != "42501":
                    fails.append(f"A-09a {t}: select raised {_state(exc)}")
                continue
            if n != 0:
                fails.append(f"A-09a {t}: {n} rows visible without milo.user_id")
        expect_state(
            "A-09a insert sessions without identity",
            app,
            "milo_app",
            None,
            "insert into sessions (user_id, title) values (%s, 'x')",
            (str(uuid.UUID(a)),),
        )
        # A-09b: owner B sees none of A's rows (counted by A's ids, not B's own rows) and changes none.
        a_uuid = str(uuid.UUID(a))
        a_sessions = col(
            op, "select id::text from sessions where user_id = %s", (a_uuid,)
        )
        a_turns = col(
            op,
            "select id::text from turns where session_id = any(%s::uuid[])",
            (a_sessions,),
        )
        if not a_sessions or not a_turns:
            fails.append("A-09b: owner A has no seeded sessions or turns to probe")
        by_user = ("user_id = %s", (a_uuid,))
        by_session = ("session_id = any(%s::uuid[])", (a_sessions,))
        by_turn = ("turn_id = any(%s::uuid[])", (a_turns,))
        where = {
            "profiles": ("id = %s", (a_uuid,)),
            "customers": by_user, "consents": by_user, "sessions": by_user,
            "field_requests": by_user, "deletion_requests": by_user,
            "messages": by_session, "turns": by_session, "facts": by_session,
            "fact_history": by_session, "evidence": by_session,
            "submissions": by_session, "approvals": by_session,
            "turn_events": by_turn, "traces": by_turn,
        }  # fmt: skip
        for t, (pred, params) in where.items():
            if (
                scalar(
                    op,
                    "milo_operator",
                    None,
                    f"select count(*) from {t} where {pred}",
                    params,
                )
                == 0
            ):
                fails.append(f"A-09b {t}: no row of owner A to probe")
            try:
                n = scalar(
                    app, "milo_app", b, f"select count(*) from {t} where {pred}", params
                )
            except Exception as exc:
                if _state(exc) != "42501":
                    fails.append(f"A-09b {t}: select raised {_state(exc)}")
                continue
            if n:
                fails.append(f"A-09b {t}: owner B sees {n} of A's rows")
            # B's write against A's rows: refused (no grant) or touching zero rows. Updates re-set a column to itself.
            col_name = (
                "title" if t == "sessions" else "id" if t in ("profiles",) else None
            )
            writes = [f"delete from {t} where {pred}"]
            if col_name:
                writes.append(f"update {t} set {col_name} = {col_name} where {pred}")
            for sql in writes:
                try:
                    with app.probe("milo_app", b) as conn:
                        touched = conn.execute(sql, params).rowcount
                except Exception as exc:
                    if _state(exc) != "42501":
                        fails.append(f"A-09b {t}: write raised {_state(exc)}")
                    continue
                if touched:
                    fails.append(
                        f"A-09b {t}: owner B's write touched {touched} of A's rows"
                    )
        # A-10b: an owner cannot change roles, and no one but the grants allows it.
        expect_state(
            "A-10b owner update profiles.role",
            app,
            "milo_app",
            a,
            "update profiles set role = 'operator' where id = %s",
            (str(uuid.UUID(a)),),
        )
        expect_state(
            "A-10b owner insert profiles",
            app,
            "milo_app",
            a,
            "insert into profiles (id, role) values (%s, 'operator')",
            (str(uuid.uuid4()),),
        )
        expect_state(
            "A-10b operator update profiles.role",
            op,
            "milo_operator",
            None,
            "update profiles set role = 'operator'",
        )
        if (
            scalar(
                op,
                "milo_operator",
                None,
                "select role from profiles where id = %s",
                (str(uuid.UUID(a)),),
            )
            != "owner"
        ):
            fails.append("A-10b: profiles.role changed")
        # A-27: the audit log is append-only for both roles; the operator may insert.
        for role, db in (("milo_app", app), ("milo_operator", op)):
            expect_state(
                f"A-27 {role} update audit_log",
                db,
                role,
                None,
                "update audit_log set action = 'x'",
            )
            expect_state(
                f"A-27 {role} delete audit_log", db, role, None, "delete from audit_log"
            )
        expect_state(
            "A-27 milo_app insert audit_log",
            app,
            "milo_app",
            None,
            "insert into audit_log (action) values ('x')",
        )
        try:
            with op.probe("milo_operator", None) as conn:
                conn.execute("insert into audit_log (action) values ('probe')")
        except Exception as exc:
            fails.append(
                f"A-27 operator insert audit_log failed: {_state(exc) or type(exc).__name__}"
            )
        if counts() != before:
            fails.append("counts: probes changed row counts")
    finally:
        app.close()
        op.close()
    return fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--postgres", action="store_true", required=True)
    args = ap.parse_args(argv)
    assert args.postgres

    from milo import settings
    from milo.store import pg_repositories
    from milo.store.db import Database
    from milo.store.migrate import admin_url

    settings.assert_not_prod()  # D76: tests and evals refuse production settings
    url = admin_url()
    admin = Database(url, admin=True)
    app = Database(
        url, set_role="milo_app"
    )  # the administrative connection, SET LOCAL ROLE per transaction
    users = [str(uuid.uuid4()), str(uuid.uuid4())]
    admin.open()
    app.open()
    try:
        for i, who in enumerate(
            users
        ):  # confirmed accounts; the trigger of D89 creates their profiles
            admin.run(
                None,
                "insert into auth.users (id, email, email_confirmed_at) values (%s, %s, now())",
                (who, f"rls-test-{i}@example.test"),
            )
        r = pg_repositories(app)
        a, b = (uuid.UUID(u).hex for u in users)
        ids = seed(r, a)
        failures = []
        for name in [*NOT_FOUND, *EMPTY, *OWN]:
            try:
                failure = check_one(r, a, b, ids, name)
            except Exception as exc:
                failure = f"{name}: unexpected {type(exc).__name__}"
            print(("FAIL " if failure else "PASS ") + (failure or name))
            if failure:
                failures.append(failure)
        print(
            f"{len(NOT_FOUND) + len(EMPTY) + len(OWN) - len(failures)} passed, {len(failures)} failed"
        )
        probe_fails = sql_probes(url, a, b)
        for group in PROBE_GROUPS:
            bad = [f for f in probe_fails if f.startswith(group)]
            print(("FAIL " if bad else "PASS ") + f"probe {group}")
            for f in bad:
                print("     " + f)
        failures += probe_fails
        print(f"table probes: {len(probe_fails)} failed")
        return 1 if failures else 0
    finally:
        for who in users:
            admin.run(None, "delete from auth.users where id = %s", (who,))
        app.close()
        admin.close()


if __name__ == "__main__":
    raise SystemExit(main())
