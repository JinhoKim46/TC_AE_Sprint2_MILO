"""The only module that creates a model client or names the OpenRouter URL (HARNESS rule 1).

`OpenRouterChat` and `OpenRouterEmbeddings` wrap `openai.OpenAI(base_url=...)`. The `openai` package is
imported inside the constructors, so `import milo.app` loads no client. The conversion of `ToolSpec` to the
OpenAI tool JSON lives here. Retries are not done here (`llm/retry.py`); this module makes one attempt.
"""

from __future__ import annotations

import json
from typing import Any

from milo.settings import CALL_TIMEOUT_S
from milo.spend import SpendLedger
from milo.types import ChatReply, ToolCall, ToolSpec, Usage

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_EMBEDDING_MODEL = "google/gemini-embedding-2"


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
    ) -> None:
        self.model = model
        self.price_usd_per_mtok = price_usd_per_mtok
        self.last_cost_usd: float | None = None
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
        if tools:
            kwargs["tools"] = [tool_json(t) for t in tools]
        resp = self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = [
            ToolCall(c.id, c.function.name, json.loads(c.function.arguments or "{}"))
            for c in (getattr(msg, "tool_calls", None) or [])
        ]
        usage = getattr(resp, "usage", None)
        cost = getattr(usage, "cost", None)
        self.last_cost_usd = float(cost) if cost is not None else None
        if self._ledger is not None and self.last_cost_usd is not None:
            self._ledger.record(
                self.last_cost_usd, model=self.model, purpose=self._purpose
            )
        return ChatReply(
            content=msg.content or "",
            tool_calls=calls,
            usage=Usage(
                getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0,
            ),
        )


class OpenRouterEmbeddings:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_EMBEDDING_MODEL,
        *,
        timeout: float = CALL_TIMEOUT_S,
        client: Any = None,
    ) -> None:
        self.model = model
        self._client = client or _make_client(api_key, timeout)

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [list(d.embedding) for d in resp.data]
