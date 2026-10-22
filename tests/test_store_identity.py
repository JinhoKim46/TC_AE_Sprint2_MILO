"""The owner filter on every repository call (SPEC D74, D91, D129; HARNESS 7 item 8).

Every owner-facing call that carries another owner's ids must fail with `not_found` and change nothing; every
call keyed only by the caller's own id must show nothing of another owner's data. The suite runs two ways:

* in pytest, against the in-memory repositories (offline);
* by the verifier against Postgres, outside pytest, with row-level security doing the filtering:

      uv run python tests/test_store_identity.py --postgres --owner-a <uuid> --owner-b <uuid> [--schema milo_<hex>]

  The verifier provisions the test project (schema, `make migrate`, two confirmed profiles) with its own
  privileges first. This script connects as `milo_app` through `settings.database_url()`, refuses production
  settings (D76) and prints PASS or FAIL per call.
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
    apid = r.approvals.create(a, sid, "delete_item", {"item_id": "1"}, turn_id=tid)[
        "id"
    ]
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--postgres", action="store_true", required=True)
    ap.add_argument("--owner-a", required=True)
    ap.add_argument("--owner-b", required=True)
    ap.add_argument("--schema", default=None)
    args = ap.parse_args(argv)

    from milo import settings
    from milo.store import pg_repositories
    from milo.store.db import Database

    settings.assert_not_prod()  # D76: tests and evals refuse production settings
    db = Database(settings.database_url(), schema=args.schema)
    db.open()
    try:
        r = pg_repositories(db)
        a, b = uuid.UUID(args.owner_a).hex, uuid.UUID(args.owner_b).hex
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
        return 1 if failures else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
