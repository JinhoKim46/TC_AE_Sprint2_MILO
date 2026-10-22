"""Chat client assembly: the cost formula, the model factory, and JSON-expected calls (SPEC 2A, 9, D23b).

This module makes no HTTP call. Model calls go through `retry.invoke_with_retry`, which is the one retry site
(HARNESS rule 10). A JSON re-ask is a second call through the same path and is not a retry.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from milo.errors import ProviderFailure
from milo.llm.retry import invoke_with_retry
from milo.llm.think import strip_think
from milo.settings import Settings
from milo.spend import SpendLedger
from milo.types import ChatModel, ChatReply, ToolSpec

JSON_REASK = "Your last reply was not valid JSON. Reply again with one JSON object only, no prose, no code fence."


def cost_usd(
    input_tokens: int, output_tokens: int, price: tuple[float, float]
) -> float:
    """SPEC section 9: (input x price_in + output x price_out) / 1_000_000, prices in USD per million."""
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def build_chat(
    model: str,
    price_usd_per_mtok: tuple[float, float],
    *,
    settings: Settings | None = None,
    ledger: SpendLedger | None = None,
    purpose: str = "chat",
    **kwargs: Any,
) -> Any:
    """The OpenRouter chat model for `model` (pinned slug from settings or the caller). Makes no call."""
    from milo.llm.cloud import OpenRouterChat

    cfg = settings or Settings.from_env()
    return OpenRouterChat(
        model,
        cfg.openrouter_api_key,
        price_usd_per_mtok,
        ledger=ledger,
        purpose=purpose,
        **kwargs,
    )


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object from a reply: think blocks and a code fence are removed first."""
    body = strip_think(text).strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    try:
        value = json.loads(body)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def invoke_json(
    chat: ChatModel,
    messages: list[dict[str, Any]],
    tools: list[ToolSpec] | None = None,
    *,
    sleep: Callable[[float], None] | None = None,
) -> tuple[dict[str, Any] | None, ChatReply, bool]:
    """One JSON-expected call with one re-ask on a parse failure (D23b).

    Returns `(parsed_or_None, last_reply, reasked)`. `None` means the caller must use its safe reply.
    Raises `ProviderFailure` only when a call itself fails after the D23a attempts.
    """
    reply = invoke_with_retry(chat, messages, tools, sleep=sleep)
    parsed = parse_json_object(reply.content)
    if parsed is not None:
        return parsed, reply, False
    again = [
        *messages,
        {"role": "assistant", "content": reply.content},
        {"role": "user", "content": JSON_REASK},
    ]
    reply2 = invoke_with_retry(chat, again, tools, sleep=sleep)
    return parse_json_object(reply2.content), reply2, True


__all__ = [
    "JSON_REASK",
    "ProviderFailure",
    "build_chat",
    "cost_usd",
    "invoke_json",
    "parse_json_object",
]
