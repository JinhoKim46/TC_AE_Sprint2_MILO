"""Contract tests: the interface a Sprint 2 project must expose. Domain-agnostic, offline, free.

Run from your project repo: `PROJECT_APP=yourpkg.app:build_app uv run pytest contract`.
Milestone selectors: `-k sources`, `-k tools` (M3), `-k security` (M4), `-k errors` (M5).
Test names carry the selector words on purpose.
"""
from __future__ import annotations

from conftest import DOCS, QUESTION
from fakes import MAX_QUESTION_CHARS, ChatReply, ProviderError, ToolCall, Usage


# Tool and security questions also quote the knowledge base, so retrieval is never empty for them
# (the abstention test covers the empty case). Apps that route tools first still pass.

def _tool_reply(name, args, call_id="c1"):
    return ChatReply(tool_calls=[ToolCall(call_id, name, args)], usage=Usage(20, 10))


# ---- M1/M2: retrieval and grounding ----

def test_sources_present_for_kb_answer(make_app):
    app, chat, _ = make_app(script=[ChatReply("Every six months.", usage=Usage(50, 10))])
    r = app.ask(QUESTION, user_id="u1", thread_id="t1")
    assert r.error is None
    assert r.answer
    assert r.sources, "an answer built from the knowledge base must list its sources"
    known = {d.doc_id for d in DOCS}
    for s in r.sources:
        assert s.doc_id in known
        assert isinstance(s.chunk_id, str) and s.chunk_id
        assert isinstance(s.score, (int, float))
    assert r.sources[0].doc_id == "kb/alpha", "the quoted passage must be the top source"


def test_abstains_without_model_call_on_empty_retrieval(make_app):
    app, chat, _ = make_app(docs=[])
    r = app.ask(QUESTION, user_id="u1", thread_id="t1")
    assert chat.calls == 0, "with nothing retrieved the model must not be called"
    assert r.answer, "abstaining still answers the user (say you do not know)"
    assert r.sources == []
    assert r.error is None
    assert r.cost_usd == 0.0


# ---- M3: tools ----

def test_tools_tool_result_shown_with_policy_decision(make_app):
    app, chat, backend = make_app(script=[_tool_reply("lookup_item", {"item_id": "42"}),
                                          ChatReply("Item 42 found.", usage=Usage(30, 8))])
    r = app.ask(f"{QUESTION} Also look up item 42.", user_id="u1", thread_id="t1")
    assert len(r.tool_results) == 1
    tr = r.tool_results[0]
    assert tr.name == "lookup_item"
    assert tr.arguments == {"item_id": "42"}
    assert tr.ok is True
    assert tr.output is not None
    assert tr.policy_decision == "allowed"
    assert [c.name for c in backend.executed] == ["lookup_item"]
    # the model saw the tool schemas the backend declared
    assert chat.seen_tools[0], "tool specs from backend.specs() must be offered to the model"


# ---- M4: security ----

def test_security_destructive_tool_needs_approval(make_app):
    app, chat, backend = make_app(script=[_tool_reply("delete_item", {"item_id": "42"}),
                                          ChatReply("done")])
    r = app.ask(f"{QUESTION} Also delete item 42.", user_id="u1", thread_id="t1")
    assert not any(c.name == "delete_item" for c in backend.executed), \
        "a destructive tool must not run without explicit approval"
    assert len(r.tool_results) == 1
    assert r.tool_results[0].policy_decision == "needs_approval"
    assert r.stopped is True


def test_security_identity_not_from_arguments(make_app):
    app, chat, backend = make_app(script=[
        _tool_reply("lookup_item", {"item_id": "42", "user_id": "u2"}),
        ChatReply("ok")])
    app.ask(f"{QUESTION} Also look up item 42.", user_id="u1", thread_id="t1")
    for c in backend.executed:
        assert c.user_id == "u1", "identity must be bound by the app, not read from model arguments"
        assert c.args.get("user_id", "u1") == "u1"
        assert "u2" not in str(c.args)


# ---- M5: errors and input validation ----

def test_errors_invalid_input_rejected_with_typed_error(make_app):
    for bad in ["", "   ", "x" * (MAX_QUESTION_CHARS + 1), "hello\x00world", "bell\x07here"]:
        app, chat, _ = make_app()
        r = app.ask(bad, user_id="u1", thread_id="t1")
        assert r.error and r.error.split(":")[0] == "invalid_input", f"input {bad[:12]!r} not rejected"
        assert chat.calls == 0
        assert r.answer, "the user still gets a message"
        assert r.tool_results == []


def test_errors_provider_error_becomes_user_message_not_traceback(make_app):
    for exc in (ProviderError("rate limited"), TimeoutError("timed out")):
        app, chat, _ = make_app(script=[exc, exc, exc, exc])
        r = app.ask(QUESTION, user_id="u1", thread_id="t1")  # must not raise
        assert r.error and r.error.split(":")[0] == "provider_error"
        assert r.answer
        assert "Traceback" not in r.answer and 'File "' not in r.answer
        assert type(exc).__name__ not in r.answer


# ---- threads and cost ----

def test_threads_isolated(make_app):
    marker = "ZEBRA-7731"
    app, chat, _ = make_app()
    app.ask(f"{QUESTION} My code is {marker}.", user_id="u1", thread_id="t1")
    app.ask(QUESTION, user_id="u1", thread_id="t2")
    t2_text = str(chat.seen[-1])
    assert marker not in t2_text, "thread t2 must not see thread t1 history"
    app.ask(QUESTION, user_id="u1", thread_id="t1")
    assert marker in str(chat.seen[-1]), "thread t1 keeps its own history"


def test_cost_reported(make_app):
    app, chat, _ = make_app(script=[ChatReply("Every six months.", usage=Usage(1000, 500))],
                            price_usd_per_mtok=(1.0, 2.0))
    r = app.ask(QUESTION, user_id="u1", thread_id="t1")
    assert abs(r.cost_usd - (1000 * 1.0 + 500 * 2.0) / 1e6) < 1e-9
