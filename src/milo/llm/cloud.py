"""The only module that creates a model client or names the OpenRouter URL (HARNESS rule 1).

`OpenRouterChat` and `OpenRouterEmbeddings` wrap `openai.OpenAI(base_url=...)`. The `openai` package is
imported inside the constructors, so `import milo.app` loads no client. The conversion of `ToolSpec` to the
OpenAI tool JSON lives here. Retries are not done here (`llm/retry.py`); this module makes one attempt per call.
"""

from __future__ import annotations

import json
from typing import Any

from milo.errors import ReasoningRequired
from milo.llm.think import strip_think
from milo.settings import CALL_TIMEOUT_S
from milo.spend import SpendLedger
from milo.types import ChatReply, ToolCall, ToolSpec, Usage

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_EMBEDDING_MODEL = "google/gemini-embedding-2"

# D159: models whose endpoint refuses `reasoning.enabled=false` (HTTP 400 "Reasoning is mandatory"). They get the
# lowest effort instead. The live runners do not load `supported_parameters` from the catalogue, so a model joins
# this set when a live call shows the 400 (the typed `ReasoningRequired` error names it).
REASONING_REQUIRED = frozenset({"anthropic/claude-sonnet-5.5"})
# OpenRouter's unified `reasoning.effort` ("low" is the lowest value every endpoint is documented to accept;
# "minimal" is not accepted by all providers, and `max_tokens` is rejected by effort-only endpoints). Thinking text
# is still stripped by `strip_think`; reasoning tokens are billed as output and are inside `usage.cost`.
REASONING_LOW_EFFORT = {"effort": "low"}
REASONING_OFF = {"enabled": False}


def reasoning_mode(model: str) -> str:
    """`"low_effort"` for a model that requires reasoning, else `"off"` (recorded in result provenance)."""
    return "low_effort" if model in REASONING_REQUIRED else "off"


def _is_reasoning_refusal(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    text = str(exc).lower()
    return (
        status == 400
        and "reasoning" in text
        and ("mandatory" in text or "cannot be disabled" in text)
    )


def _make_client(api_key: str, timeout: float) -> Any:
    from openai import OpenAI

    return OpenAI(
        base_url=OPENROUTER_BASE_URL, api_key=api_key, timeout=timeout, max_retries=0
    )


def tool_json(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map MILO message dicts (role, content, optional tool_calls / tool_call_id) to the OpenAI shape."""
    out: list[dict[str, Any]] = []
    for m in messages:
        item: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
        if m.get("tool_calls"):
            item["tool_calls"] = [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {"name": c["name"], "arguments": json.dumps(c["args"])},
                }
                for c in m["tool_calls"]
            ]
        if m["role"] == "tool":
            item["tool_call_id"] = m["tool_call_id"]
        out.append(item)
    return out


def _tool_args(raw: str | None) -> dict[str, Any]:
    """Tool arguments from the model; malformed or non-object JSON becomes `{}` (the tool validator rejects it)."""
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def fetch_catalogue(api_key: str, *, client: Any = None) -> dict[str, dict[str, Any]]:
    """The OpenRouter models catalogue as `{slug: {"prompt": usd_per_token, "completion": ..., "context": n}}`.

    Used by the E02 runner to check each candidate slug before any paid call. `client` is injectable so tests
    pass a recorded response; with no client this makes one network call.
    """
    cli = client or _make_client(api_key, CALL_TIMEOUT_S)
    out: dict[str, dict[str, Any]] = {}
    for item in cli.models.list():
        row = item if isinstance(item, dict) else item.model_dump()
        pricing = row.get("pricing") or {}
        try:
            prompt = float(pricing["prompt"])
            completion = float(pricing["completion"])
        except (KeyError, TypeError, ValueError):
            prompt = completion = float("nan")
        out[str(row["id"])] = {
            "prompt": prompt,
            "completion": completion,
            "context": row.get("context_length"),
        }
    return out


class OpenRouterChat:
    def __init__(
        self,
        model: str,
        api_key: str,
        price_usd_per_mtok: tuple[float, float],
        *,
        timeout: float = CALL_TIMEOUT_S,
        client: Any = None,
        ledger: SpendLedger | None = None,
        purpose: str = "chat",
        temperature: float | None = 0.0,
        disable_thinking: bool = True,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.disable_thinking = disable_thinking
        self.price_usd_per_mtok = price_usd_per_mtok
        self.last_cost_usd: float | None = None
        self.last_response_model: str | None = (
            None  # the `model` string the provider answered with
        )
        self._client = client or _make_client(api_key, timeout)
        self._ledger = ledger
        self._purpose = purpose

    def invoke(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec] | None = None
    ) -> ChatReply:
        if self._ledger is not None:
            self._ledger.check()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": to_openai_messages(messages),
            "extra_body": {"usage": {"include": True}},
        }
        if self.disable_thinking:  # D23b, amended by D159
            kwargs["extra_body"]["reasoning"] = dict(
                REASONING_LOW_EFFORT
                if self.model in REASONING_REQUIRED
                else REASONING_OFF
            )
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if tools:
            kwargs["tools"] = [tool_json(t) for t in tools]
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if _is_reasoning_refusal(exc):
                raise ReasoningRequired(self.model) from exc
            raise
        msg = resp.choices[0].message
        served = getattr(resp, "model", None)
        self.last_response_model = served if isinstance(served, str) else None
        calls = [
            ToolCall(c.id, c.function.name, _tool_args(c.function.arguments))
            for c in (getattr(msg, "tool_calls", None) or [])
        ]
        usage = getattr(resp, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0
        cost = getattr(usage, "cost", None)
        # `usage.cost` already includes reasoning tokens (billed as output); `completion_tokens` includes them too.
        if cost is not None:  # the provider's own figure wins (SPEC 2A)
            self.last_cost_usd = float(cost)
        elif (
            usage is not None
        ):  # fall back to the section 9 formula with the pinned price
            self.last_cost_usd = (
                tokens_in * self.price_usd_per_mtok[0]
                + tokens_out * self.price_usd_per_mtok[1]
            ) / 1_000_000
        else:
            self.last_cost_usd = None
        if self._ledger is not None and self.last_cost_usd is not None:
            self._ledger.record(
                self.last_cost_usd, model=self.model, purpose=self._purpose
            )
        return ChatReply(
            content=strip_think(msg.content or ""),
            tool_calls=calls,
            usage=Usage(tokens_in, tokens_out),
        )


class OpenRouterEmbeddings:
    """Embedding client. Keeps the usage of every call: `last_tokens`, `last_cost_usd`, and running totals.

    Cost is the provider's `usage.cost` when OpenRouter returns it, else tokens times `price_usd_per_mtok`
    when a price is given, else unknown (`None`; the totals then count only the known calls and
    `cost_unknown_calls` says how many were not priced).
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_EMBEDDING_MODEL,
        *,
        timeout: float = CALL_TIMEOUT_S,
        client: Any = None,
        price_usd_per_mtok: float | None = None,
    ) -> None:
        self.model = model
        self.price_usd_per_mtok = price_usd_per_mtok
        self._client = client or _make_client(api_key, timeout)
        self.last_tokens: int | None = None
        self.last_cost_usd: float | None = None
        self.total_tokens = 0
        self.total_cost_usd = 0.0
        self.cost_unknown_calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.model, input=texts)
        usage = getattr(resp, "usage", None)
        tokens = getattr(usage, "total_tokens", None)
        if tokens is None:
            tokens = getattr(usage, "prompt_tokens", None)
        cost = getattr(usage, "cost", None)
        if cost is not None:
            self.last_cost_usd = float(cost)
        elif tokens is not None and self.price_usd_per_mtok is not None:
            self.last_cost_usd = tokens * self.price_usd_per_mtok / 1_000_000
        else:
            self.last_cost_usd = None
        self.last_tokens = int(tokens) if tokens is not None else None
        self.total_tokens += self.last_tokens or 0
        if self.last_cost_usd is None:
            self.cost_unknown_calls += 1
        else:
            self.total_cost_usd += self.last_cost_usd
        data = sorted(resp.data, key=lambda d: getattr(d, "index", 0) or 0)
        return [list(d.embedding) for d in data]
