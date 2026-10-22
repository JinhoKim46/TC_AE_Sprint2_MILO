"""M4 identity tests (SPEC D21, section 8): the backend gets the session's `user_id`, identity keys never reach it.

M3's `tests/test_tools.py` covers `clean_args` and the backend's own stripping. These tests go through the whole
app (validate, retrieve, loop, policy, backend), as threat-model case A-07 does. Offline, scripted fakes.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from fakes import HashEmbedder, ScriptedChat

from milo.app import build_app
from milo.security.identity import (
    IDENTITY_KEYS,
    clean_identity,
    fold_key,
    strip_identity,
    strip_lookalikes,
)
from milo.tools import MiloBackend
from milo.tools.args import clean_args
from milo.types import ChatReply, Source, ToolCall, Usage

OWNER_A, OWNER_B = "owner-a", "owner-b"
EXACT = {k: OWNER_B for k in ("user_id", "session_id", "owner_id", "thread_id")}
LOOKALIKES = {
    "userId": OWNER_B,
    "USER_ID": OWNER_B,
    "owner-id": OWNER_B,
    "Session Id": OWNER_B,
}
FULL_WIDTH = {"ｕｓｅｒ＿ｉｄ": OWNER_B}


class StubIndex:
    def search(
        self, query: str, *, filters: dict[str, Any] | None = None
    ) -> list[Source]:
        return [
            Source("kb/x#0", "kb/x", 0.9, "Missed calls are covered by phone-intake.")
        ]


def setup(script: list[Any]) -> tuple[Any, ScriptedChat, MiloBackend]:
    backend = MiloBackend(retriever=StubIndex())
    chat = ScriptedChat(script)
    app = build_app(chat, HashEmbedder(), backend, vector_store=StubIndex())
    return app, chat, backend


def reply(name: str, args: dict[str, Any]) -> ChatReply:
    return ChatReply(tool_calls=[ToolCall("c1", name, args)], usage=Usage(10, 5))


def assert_no_identity_at_backend(backend: MiloBackend) -> None:
    for call in backend.executed:
        assert call.user_id == OWNER_A
        assert not (set(call.args) & IDENTITY_KEYS)
        assert OWNER_B not in str(call.args)
        assert all(
            fold_key(k) not in {"userid", "sessionid", "ownerid", "threadid"}
            for k in call.args
        )


VALID_ARGS: dict[str, dict[str, Any]] = {
    "search_knowledge": {"query": "missed calls", "product": None},
    "get_session_spec": {},
    "estimate_cost": {"module_ids": ["phone-intake"]},
    "record_fact": {
        "field_id": "pain_points",
        "value": ["missed calls"],
        "unit": None,
        "period": None,
        "evidence_text": "missed calls",
    },
}


@pytest.mark.parametrize("tool", sorted(VALID_ARGS))
@pytest.mark.parametrize(
    "extra",
    [EXACT, LOOKALIKES, FULL_WIDTH, {**EXACT, **LOOKALIKES}],
    ids=["exact", "lookalike", "fullwidth", "all"],
)
def test_allowed_call_runs_with_the_session_identity_and_stripped_args(
    tool: str, extra: dict[str, str]
) -> None:
    args = {**VALID_ARGS[tool], **extra}
    app, _, backend = setup([reply(tool, args), ChatReply("done")])
    r = app.ask("missed calls", user_id=OWNER_A, thread_id="S-A2")
    assert r.error is None
    assert backend.executed, (
        "an allowed call must reach the backend"
    )  # not a vacuous pass
    assert [c.name for c in backend.executed] == [tool]
    assert r.tool_results[0].policy_decision == "allowed" and r.tool_results[0].ok
    assert (
        backend.executed[0].args == VALID_ARGS[tool]
    )  # exactly the model's own arguments, nothing else
    assert r.tool_results[0].arguments == VALID_ARGS[tool]
    assert_no_identity_at_backend(backend)


@pytest.mark.parametrize("tool", sorted(VALID_ARGS))
def test_bare_short_names_are_not_stripped_but_the_closed_schema_rejects_them(
    tool: str,
) -> None:
    for extra in ({"uid": OWNER_B}, {"owner": OWNER_B}, {"sessionId2": OWNER_B}):
        app, _, backend = setup(
            [reply(tool, {**VALID_ARGS[tool], **extra}), ChatReply("x")]
        )
        r = app.ask("missed calls", user_id=OWNER_A, thread_id="S-A2")
        assert r.error is None
        assert backend.executed == []  # the backend never ran
        assert r.tool_results[0].policy_decision == "denied"
        assert r.tool_results[0].output["error"] == "invalid_arguments"


def test_the_backend_receives_the_ask_user_id_even_when_the_model_names_another_owner() -> (
    None
):
    app, _, backend = setup([reply("get_session_spec", dict(EXACT)), ChatReply("ok")])
    app.ask("missed calls", user_id=OWNER_A, thread_id="S-A2")
    assert [c.user_id for c in backend.executed] == [OWNER_A]
    app2, _, backend2 = setup([reply("get_session_spec", {}), ChatReply("ok")])
    app2.ask("missed calls", user_id=OWNER_B, thread_id="S-B1")
    assert [c.user_id for c in backend2.executed] == [OWNER_B]


def test_withdraw_runs_after_approval_with_the_bound_identity_and_stripped_args() -> (
    None
):
    args = {"submission_id": 1, **EXACT, **LOOKALIKES}
    app, _, backend = setup([reply("withdraw_submission", args), ChatReply("Done.")])
    r = app.ask("withdraw my submission", user_id=OWNER_A, thread_id="S-A2")
    assert r.stopped and backend.executed == []  # nothing runs before approval
    approval_id = r.tool_results[0].output["approval_id"]
    done = app.resume_approval(approval_id, "approve", OWNER_A)
    assert done.error is None
    assert backend.executed, "the approved call must reach the backend"
    assert backend.executed[0].args == {"submission_id": 1}
    assert backend.executed[0].state_changed is True
    assert_no_identity_at_backend(backend)


def test_cross_owner_submission_after_approval_is_not_found_and_changes_nothing() -> (
    None
):
    args = {"submission_id": 2, **EXACT}  # submission 2 is owner-b's
    app, _, backend = setup([reply("withdraw_submission", args), ChatReply("Done.")])
    r = app.ask("withdraw my submission", user_id=OWNER_A, thread_id="S-A2")
    done = app.resume_approval(
        r.tool_results[0].output["approval_id"], "approve", OWNER_A
    )
    assert done.tool_results[0].output["status"] == "not_found"
    assert backend.executed and backend.executed[0].user_id == OWNER_A
    assert all(not c.state_changed for c in backend.executed)
    store = backend.store
    assert store.submission_status(2) == "received"  # type: ignore[union-attr]
    assert store.submission_status(1) == "received"  # type: ignore[union-attr]


# ---- the functions themselves ----


def test_strip_identity_removes_exact_keys_at_any_depth_and_keeps_values() -> None:
    raw = {
        "query": "user_id",  # a value is never touched
        "user_id": "x",
        "nested": {"owner_id": "x", "keep": [{"thread_id": "x", "n": 1}, "session_id"]},
    }
    assert strip_identity(raw) == {
        "query": "user_id",
        "nested": {"keep": [{"n": 1}, "session_id"]},
    }


def test_lookalikes_fold_case_width_punctuation_and_padding() -> None:
    for key in (
        "userId",
        "USER_ID",
        "user-id",
        " user_id ",
        "User.Id",
        "ｏｗｎｅｒ＿ｉｄ",
        "thread id",
    ):
        assert strip_lookalikes({key: 1, "keep": 2}) == {"keep": 2}, key
    for key in ("uid", "owner", "user", "session", "userid2", "users_id"):
        assert strip_lookalikes({key: 1}) == {key: 1}, (
            key
        )  # not identity keys; the schema rejects them


def test_clean_identity_does_not_mutate_its_input_and_clean_args_delegates() -> None:
    raw = {"a": [{"userId": 1, "user_id": 2, "b": 3}], "c": {"owner-id": 4}}
    before = copy.deepcopy(raw)
    out = clean_identity(raw)
    assert raw == before
    assert out == {"a": [{"b": 3}], "c": {}}
    assert clean_args(raw) == out
    assert clean_identity("text") == "text" and clean_identity(None) is None
