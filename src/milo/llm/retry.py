"""The one retry site for model calls (SPEC D23a, HARNESS rule 10). S0 seam; M5a replaces the body.

Any exception from `chat.invoke` is a provider failure: `TimeoutError` (or a class name containing "Timeout")
gives `provider_error:timeout`, anything else `provider_error:unavailable`. At most 3 attempts, with backoff
0.5 s and 1.0 s through the injected `sleep`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from milo.errors import ProviderFailure
from milo.settings import RETRY_ATTEMPTS, RETRY_BACKOFF_S
from milo.types import ChatModel, ChatReply, ToolSpec


def classify_exception(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        return "timeout"
    return "unavailable"


def invoke_with_retry(
    chat: ChatModel,
    messages: list[dict[str, Any]],
    tools: list[ToolSpec] | None,
    *,
    sleep: Callable[[float], None] | None = None,
    attempts: int = RETRY_ATTEMPTS,
    backoff: tuple[float, ...] = RETRY_BACKOFF_S,
) -> ChatReply:
    """Call the model, retrying a failed call. Raises `ProviderFailure` after the last attempt."""
    pause = sleep or (lambda _s: None)
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return chat.invoke(messages, tools)
        except (
            Exception
        ) as exc:  # any provider failure is retried, then mapped to a typed error
            last = exc
            if attempt < attempts - 1:
                pause(backoff[min(attempt, len(backoff) - 1)])
    assert last is not None
    raise ProviderFailure(classify_exception(last)) from last
