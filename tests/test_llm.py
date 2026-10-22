"""M5a: LLM client tests. Offline; the OpenAI client is a scripted fake (HARNESS rule 2)."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fakes import ChatReply, ProviderError, ScriptedChat, Usage

from milo.errors import ProviderFailure, ReasoningRequired, SpendRefused
from milo.llm import cloud
from milo.llm.client import cost_usd, invoke_json, parse_json_object
from milo.llm.retry import classify_exception, invoke_with_retry, is_retryable
from milo.llm.think import strip_think
from milo.llm.tokens import count_message_tokens, count_tokens
from milo.loop import call_cost
from milo.spend import SpendLedger

ROOT = Path(__file__).resolve().parents[1]


class FakeClient:
    """Stands in for `openai.OpenAI`: replays responses or raises."""

    def __init__(self, script, models=None):
        self.script = list(script)
        self.requests = []
        self._models = models or []
        self.chat = NS(completions=NS(create=self._create))
        self.models = NS(list=lambda: self._models)

    def _create(self, **kw):
        self.requests.append(kw)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def resp(content="hi", cost=None, pin=10, pout=5, tool_calls=None):
    usage = NS(prompt_tokens=pin, completion_tokens=pout, cost=cost)
    msg = NS(content=content, tool_calls=tool_calls)
    return NS(choices=[NS(message=msg)], usage=usage)


class HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"http {status}")
        self.status_code = status


# ---- think stripping ----


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("<think>plan</think>Answer", "Answer"),
        ('<think>a\nb</think>\n\n{"x": 1}', '{"x": 1}'),
        ("A<think>x</think>B<THINK>y</THINK>C", "ABC"),
        ("Answer<think>never closed", "Answer"),
        ("reasoning only</think>Answer", "Answer"),
        ("<think>only thinking</think>", ""),
        ("plain", "plain"),
        ("", ""),
    ],
)
def test_strip_think(raw, clean):
    assert strip_think(raw) == clean


def test_chat_strips_think_before_returning():
    chat = cloud.OpenRouterChat(
        "m", "k", (1, 2), client=FakeClient([resp("<think>x</think>Done")])
    )
    assert chat.invoke([{"role": "user", "content": "q"}]).content == "Done"


def test_parse_json_object_handles_think_and_fence():
    assert parse_json_object('<think>t</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object("[1]") is None
    assert parse_json_object("not json") is None


def test_invoke_json_reasks_once_then_gives_up():
    chat = ScriptedChat([ChatReply("nope"), ChatReply('{"ok": true}')])
    parsed, _, reasked = invoke_json(chat, [{"role": "user", "content": "q"}])
    assert parsed == {"ok": True} and reasked and chat.calls == 2
    chat = ScriptedChat([ChatReply("nope"), ChatReply("still nope")])
    parsed, _, reasked = invoke_json(chat, [])
    assert parsed is None and reasked and chat.calls == 2


def test_json_reask_failure_is_not_retried_as_a_provider_error():
    chat = ScriptedChat([ChatReply("bad")] * 5)
    invoke_json(chat, [])
    assert chat.calls == 2  # no extra attempts for a parse failure


# ---- cost ----


def test_cost_formula_matches_loop_and_spec():
    assert cost_usd(1000, 500, (1.0, 2.0)) == pytest.approx(0.002)
    reply = ChatReply("x", usage=Usage(1234, 567))
    assert cost_usd(1234, 567, (0.4, 1.6)) == pytest.approx(
        call_cost(reply, (0.4, 1.6))
    )


def test_cost_from_usage_field_and_ledger(tmp_path):
    ledger = SpendLedger(tmp_path / "spend.jsonl", budget_usd=5.0)
    client = FakeClient([resp(cost=0.0123, pin=100, pout=50)])
    chat = cloud.OpenRouterChat("m", "k", (1, 2), client=client, ledger=ledger)
    reply = chat.invoke([{"role": "user", "content": "q"}])
    assert chat.last_cost_usd == pytest.approx(0.0123)
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (100, 50)
    assert ledger.spent() == pytest.approx(0.0123)
    assert client.requests[0]["extra_body"]["usage"] == {"include": True}


def test_cost_falls_back_to_formula_without_usage_cost():
    chat = cloud.OpenRouterChat(
        "m", "k", (1.0, 2.0), client=FakeClient([resp(cost=None, pin=1000, pout=500)])
    )
    chat.invoke([])
    assert chat.last_cost_usd == pytest.approx(0.002)


def test_ledger_refusal_makes_no_call(tmp_path):
    ledger = SpendLedger(tmp_path / "s.jsonl", budget_usd=0.5)  # below the reserve
    client = FakeClient([resp()])
    chat = cloud.OpenRouterChat("m", "k", (1, 2), client=client, ledger=ledger)
    with pytest.raises(SpendRefused):
        chat.invoke([])
    assert client.requests == []


def test_request_shape_pins_model_and_disables_thinking():
    client = FakeClient([resp()])
    cloud.OpenRouterChat("vendor/model-1", "k", (1, 2), client=client).invoke(
        [{"role": "user", "content": "q"}]
    )
    req = client.requests[0]
    assert req["model"] == "vendor/model-1" and req["temperature"] == 0.0
    assert req["extra_body"]["reasoning"] == {"enabled": False}
    assert req["messages"] == [{"role": "user", "content": "q"}]


def test_reasoning_request_body_per_model_d159():
    body = {}
    for model in ("anthropic/claude-sonnet-5.5", "anthropic/claude-haiku-5.5"):
        client = FakeClient([resp()])
        cloud.OpenRouterChat(model, "k", (1, 2), client=client).invoke([])
        body[model] = client.requests[0]["extra_body"]["reasoning"]
    assert body["anthropic/claude-sonnet-5.5"] == {"effort": "low"}
    assert body["anthropic/claude-haiku-5.5"] == {"enabled": False}
    assert cloud.reasoning_mode("anthropic/claude-sonnet-5.5") == "low_effort"
    assert cloud.reasoning_mode("openai/gpt-4.1-mini") == "off"


def test_reasoning_tokens_stay_in_cost_and_thinking_is_stripped():
    usage = NS(
        prompt_tokens=100,
        completion_tokens=900,  # includes 800 reasoning tokens
        completion_tokens_details=NS(reasoning_tokens=800),
        cost=0.0123,
    )
    r = NS(
        choices=[NS(message=NS(content="<think>x</think>ok", tool_calls=None))],
        usage=usage,
    )
    chat = cloud.OpenRouterChat(
        "anthropic/claude-sonnet-5.5", "k", (2, 10), client=FakeClient([r])
    )
    reply = chat.invoke([])
    assert reply.content == "ok" and chat.last_cost_usd == pytest.approx(0.0123)
    assert reply.usage.output_tokens == 900


def test_reasoning_mandatory_400_is_a_typed_error_naming_the_model_and_not_retried():
    msg = "Reasoning is mandatory for this endpoint and cannot be disabled"
    err = HttpError(400)
    err.args = (msg,)
    client = FakeClient([err, resp()])
    chat = cloud.OpenRouterChat("vendor/other-model", "k", (1, 2), client=client)
    with pytest.raises(ReasoningRequired) as info:
        invoke_with_retry(chat, [], None)
    assert info.value.error == "provider_error:reasoning_required:vendor/other-model"
    assert info.value.error.split(":")[0] == "provider_error"
    assert isinstance(info.value, ProviderFailure) and len(client.requests) == 1
    assert msg in str(info.value.__cause__)


def test_other_400_is_not_mapped_to_reasoning_required():
    client = FakeClient([HttpError(400)])
    chat = cloud.OpenRouterChat("m", "k", (1, 2), client=client)
    with pytest.raises(ProviderFailure) as info:
        invoke_with_retry(chat, [], None)
    assert not isinstance(info.value, ReasoningRequired)


def test_tool_call_arguments_parsed_and_bad_json_is_empty():
    calls = [
        NS(id="c1", function=NS(name="t", arguments='{"a": 1}')),
        NS(id="c2", function=NS(name="t", arguments="{broken")),
    ]
    chat = cloud.OpenRouterChat(
        "m", "k", (1, 2), client=FakeClient([resp(None, tool_calls=calls)])
    )
    got = chat.invoke([]).tool_calls
    assert [(c.id, c.args) for c in got] == [("c1", {"a": 1}), ("c2", {})]


# ---- tokens ----


def test_token_counts():
    assert (
        count_tokens("") == 0
        and count_tokens("abcd") == 1
        and count_tokens("abcde") == 2
    )
    assert count_message_tokens([{"role": "user", "content": "abcd"}]) == 5


# ---- retry classification ----


def test_classification():
    assert classify_exception(TimeoutError()) == "timeout"
    assert classify_exception(type("APITimeoutError", (Exception,), {})()) == "timeout"
    assert classify_exception(ProviderError("x")) == "unavailable"
    for status in (408, 429, 500, 502, 503, 529):
        assert is_retryable(HttpError(status)), status
    for status in (400, 401, 402, 403, 404, 422):
        assert not is_retryable(HttpError(status)), status
    assert is_retryable(ProviderError("no status"))
    assert not is_retryable(SpendRefused("x"))


def test_retry_attempts_and_backoff():
    slept: list[float] = []
    chat = ScriptedChat([HttpError(429), HttpError(503), ChatReply("ok")])
    assert invoke_with_retry(chat, [], None, sleep=slept.append).content == "ok"
    assert chat.calls == 3 and slept == [0.5, 1.0]


def test_retry_gives_up_after_three_with_typed_error():
    chat = ScriptedChat([HttpError(500)] * 4)
    with pytest.raises(ProviderFailure) as info:
        invoke_with_retry(chat, [], None)
    assert info.value.error == "provider_error:unavailable" and chat.calls == 3


def test_permanent_error_is_not_retried():
    slept: list[float] = []
    chat = ScriptedChat([HttpError(401), ChatReply("never")])
    with pytest.raises(ProviderFailure) as info:
        invoke_with_retry(chat, [], None, sleep=slept.append)
    assert chat.calls == 1 and slept == []
    assert info.value.error == "provider_error:unavailable"


def test_spend_refusal_is_not_retried_and_maps_to_provider_error():
    chat = ScriptedChat([SpendRefused("budget"), ChatReply("never")])
    with pytest.raises(ProviderFailure):
        invoke_with_retry(chat, [], None)
    assert chat.calls == 1


def test_client_does_not_retry_itself():
    client = FakeClient([HttpError(500), resp()])
    chat = cloud.OpenRouterChat("m", "k", (1, 2), client=client)
    with pytest.raises(HttpError):
        chat.invoke([])
    assert len(client.requests) == 1


def test_sdk_client_built_with_zero_internal_retries(monkeypatch):
    seen = {}

    class Stub:
        def __init__(self, **kw):
            seen.update(kw)

    import openai

    monkeypatch.setattr(openai, "OpenAI", Stub)
    cloud._make_client("k", 5.0)
    assert seen["max_retries"] == 0 and seen["timeout"] == 5.0


def test_exactly_one_retry_site():
    """Only llm/retry.py loops over attempts around a model call (HARNESS rule 10)."""
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if rel == "src/milo/llm/retry.py":
            continue
        if re.search(r"RETRY_ATTEMPTS|RETRY_BACKOFF_S|max_retries\s*=\s*[1-9]", text):
            if rel not in {"src/milo/settings.py"}:
                offenders.append(rel)
        if re.search(r"for\s+\w+\s+in\s+range\([^)]*attempt", text):
            offenders.append(rel)
    assert offenders == []
    assert (ROOT / "src/milo/llm/retry.py").read_text().count("chat.invoke(") == 1


# ---- catalogue ----


def test_fetch_catalogue_from_recorded_response():
    fixture = [
        {
            "id": "a/one",
            "pricing": {"prompt": "0.0000004", "completion": "0.0000016"},
            "context_length": 1000,
        },
        NS(model_dump=lambda: {"id": "b/two", "pricing": {}, "context_length": 5}),
    ]
    cat = cloud.fetch_catalogue("k", client=FakeClient([], models=fixture))
    assert cat["a/one"] == {"prompt": 4e-7, "completion": 1.6e-6, "context": 1000}
    assert cat["b/two"]["prompt"] != cat["b/two"]["prompt"]  # NaN: price missing


# ---- E02 runner (offline: recorded catalogue and fixture) ----

import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

CATALOGUE_FIXTURE = [
    {
        "id": "openai/gpt-4.1-mini",
        "pricing": {"prompt": "0.0000004", "completion": "0.0000016"},
        "context_length": 1047576,
    },
    {
        "id": "openai/gpt-4.1-nano",
        "pricing": {"prompt": "0.0000001", "completion": "0.0000004"},
        "context_length": 1047576,
    },
    {
        "id": "google/gemini-2.5-flash-lite",
        "pricing": {"prompt": "0.0000001"},
        "context_length": 1000000,
    },
]


def _compare():
    sys.path.insert(0, str(ROOT / "evals" / "chat"))
    import compare_chat

    return compare_chat


def test_e02_verifies_slugs_against_recorded_catalogue():
    cc = _compare()
    cat = cloud.fetch_catalogue("k", client=FakeClient([], models=CATALOGUE_FIXTURE))
    got = cc.verify_slugs(
        cat,
        {
            "gpt-4.1-nano": "openai/gpt-4.1-nano",
            "gemini-2.5-flash-lite": "google/gemini-2.5-flash-lite",
            "gpt-5-nano": "openai/gpt-5-nano",
        },
    )
    assert set(got["pinned"]) == {"openai/gpt-4.1-mini", "openai/gpt-4.1-nano"}
    reasons = {d["slug"]: d["reason"] for d in got["dropped"]}
    assert reasons == {
        "google/gemini-2.5-flash-lite": "price missing",
        "openai/gpt-5-nano": "not listed in the catalogue",
    }
    with pytest.raises(RuntimeError):
        cc.verify_slugs({}, {})  # the reference must be listed


def test_e02_scoring_rules():
    cc = _compare()
    exp = [{"field_id": "daily_request_volume", "value": 35}]
    fact = {
        "field_id": "daily_request_volume",
        "value": 700,
        "period": "month",
        "evidence_text": "about  700 a month",
    }
    s = cc.score_turn(
        exp, [], [fact], "about 700 a month"
    )  # 700 / 21 = 33.3, outside 1 percent
    assert (s["tp"], s["fp"], s["fn"]) == (0, 1, 1) and s["verbatim"] == 1
    fact["value"] = 735
    assert cc.score_turn(exp, [], [fact], "about 700 a month")["tp"] == 1
    assert cc.values_match(["a", "b"], ["B", "a"]) and cc.values_match(4, "four")
    row = {
        "answerable": True,
        "context_ids": ["c1", "c2"],
        "gold_chunk_ids": ["c1"],
        "reply": "Yes [c1]",
    }
    assert cc.score_reply(row)["citation_ok"] is True
    assert cc.score_reply({**row, "reply": "Yes [c1] [c9]"})["citation_ok"] is False
    assert (
        cc.score_reply({**row, "reply": "INSUFFICIENT_CONTEXT"})["abstain_ok"] is False
    )
    unans = {
        **row,
        "answerable": False,
        "gold_chunk_ids": [],
        "reply": "INSUFFICIENT_CONTEXT",
    }
    assert cc.score_reply(unans)["abstain_ok"] is True


def _run(*args, env=None):
    import os

    return subprocess.run(
        [sys.executable, str(ROOT / "evals/chat/compare_chat.py"), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "MILO_LIVE": "", **(env or {})},
    )


def test_e02_replay_of_committed_fixture_matches_and_detects_tampering(tmp_path):
    fixture = ROOT / "evals/chat/fixtures/chat_dev_fixture.json"
    ok = _run("--replay", "--result", str(fixture))
    assert ok.returncode == 0 and "replay OK" in ok.stdout
    data = json.loads(fixture.read_text())
    data["metrics"][next(iter(data["metrics"]))]["f1"] = 0.123
    bad = tmp_path / "tampered.json"
    bad.write_text(json.dumps(data))
    res = _run("--replay", "--result", str(bad))
    assert res.returncode == 1 and "MISMATCH" in res.stderr


def test_e02_live_refuses_without_milo_live():
    res = _run("--live")
    assert res.returncode != 0 and "MILO_LIVE=1" in res.stderr


def test_e02_test_conversations_sealed(monkeypatch):
    cc = _compare()
    monkeypatch.delenv("MILO_FINAL", raising=False)
    with pytest.raises(PermissionError):
        cc.load_conversations("test")
    assert set(cc.load_conversations("dev")) == {"A-1", "B-1", "E-1"}


def test_e02_row_keeps_provider_message_and_provenance_records_reasoning():
    cc = _compare()
    msg = "Reasoning is mandatory for this endpoint and cannot be disabled"
    err = HttpError(400)
    err.args = (msg,)
    chat = cloud.OpenRouterChat(
        "anthropic/claude-sonnet-5.5", "k", (2, 10), client=FakeClient([err])
    )
    row = cc.run_row(
        chat, "anthropic/claude-sonnet-5.5", [], json_expected=False, sleep=None
    )
    assert row["error"].startswith("provider_error:reasoning_required")
    assert msg in row["provider_message"]
    from evals import common

    prov = common.provenance(
        model="anthropic/claude-sonnet-5.5,openai/gpt-4.1-mini",
        seed=0,
        temperature=0.0,
        command="c",
        inputs={},
    )
    assert prov["reasoning"] == {
        "anthropic/claude-sonnet-5.5": "low_effort",
        "openai/gpt-4.1-mini": "off",
    }
    assert "reasoning" not in common.provenance(
        model="none", seed=0, temperature=0.0, command="c", inputs={}
    )
