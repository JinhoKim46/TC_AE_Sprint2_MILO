"""M5a: typed errors and the user-facing mapping (SPEC D23). Offline."""

from __future__ import annotations

import pytest
from fakes import (
    ChatReply,
    Document,
    FakeBackend,
    HashEmbedder,
    ProviderError,
    ScriptedChat,
)

from milo.app import build_app
from milo.errors import (
    InvalidInput,
    NotFound,
    PolicyDenied,
    ProviderFailure,
    SpendRefused,
    format_error,
    user_message,
)
from milo.llm.retry import invoke_with_retry

QUESTION = "How often does the alpha widget need a firmware update every six months?"
DOCS = [
    Document(
        "kb/alpha",
        "The alpha widget needs a firmware update every six months.",
        {"section": "hardware"},
    )
]


def test_error_strings_use_code_colon_detail():
    assert format_error("provider_error", "timeout") == "provider_error:timeout"
    assert format_error("policy_denied") == "policy_denied"
    assert InvalidInput("empty").error == "invalid_input:empty"
    assert ProviderFailure("unavailable").error == "provider_error:unavailable"
    assert PolicyDenied().error == "policy_denied"
    assert NotFound().error == "not_found"
    assert SpendRefused("budget below reserve").error.split(":")[0] == "spend_refused"


@pytest.mark.parametrize(
    "error",
    [
        "invalid_input:empty",
        "invalid_input:too_long",
        "invalid_input:control_characters",
        "provider_error:timeout",
        "provider_error:unavailable",
        "policy_denied",
        "not_found",
        "unknown_code:detail",
    ],
)
def test_user_message_is_plain(error):
    msg = user_message(error)
    assert (
        msg
        and "Traceback" not in msg
        and "Error" not in msg
        and "provider_error" not in msg
    )


def test_provider_failures_reach_the_user_as_messages_not_tracebacks():
    for exc in (ProviderError("rate limited"), TimeoutError("slow")):
        chat = ScriptedChat([exc] * 4)
        app = build_app(chat, HashEmbedder(), FakeBackend())
        app.ingest(DOCS)
        r = app.ask(QUESTION, user_id="u1", thread_id="t1")
        assert r.error and r.error.split(":")[0] == "provider_error"
        assert r.answer == user_message(r.error)
        assert type(exc).__name__ not in r.answer and 'File "' not in r.answer


def test_timeout_and_unavailable_details_through_retry():
    with pytest.raises(ProviderFailure) as t:
        invoke_with_retry(ScriptedChat([TimeoutError()] * 3), [], None)
    with pytest.raises(ProviderFailure) as u:
        invoke_with_retry(ScriptedChat([ProviderError("x")] * 3), [], None)
    assert (t.value.error, u.value.error) == (
        "provider_error:timeout",
        "provider_error:unavailable",
    )


def test_invalid_input_makes_no_model_call():
    chat = ScriptedChat([ChatReply("never")])
    app = build_app(chat, HashEmbedder(), FakeBackend())
    for bad in ("", "x" * 5000, "a\x00b"):
        r = app.ask(bad, user_id="u1", thread_id="t1")
        assert r.error and r.error.split(":")[0] == "invalid_input"
    assert chat.calls == 0
