"""The one retry site for model calls (SPEC D23a, HARNESS rule 10).

Classification of a failed attempt (duck-typed, so this module imports no provider package):

- timeout: `TimeoutError`, or a class name containing "Timeout"; retried; final error `provider_error:timeout`.
- retryable: HTTP 408, 409, 425, 429 and 5xx, connection errors, and any exception without a status; retried.
- permanent: HTTP 400, 401, 402, 403, 404, 413 and 422 (the same call cannot succeed); not retried.
- spend refusal (`SpendRefused`): a local decision, not a provider failure; not retried.

Every final failure maps to `ProviderFailure` (`provider_error:timeout` or `provider_error:unavailable`, D23).
At most 3 attempts with backoff 0.5 s and 1.0 s through the injected `sleep`. A JSON re-ask is a separate call
through this function and is not a retry (HARNESS rule 10).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from milo.errors import ProviderFailure, SpendRefused
from milo.settings import RETRY_ATTEMPTS, RETRY_BACKOFF_S
from milo.types import ChatModel, ChatReply, ToolSpec

PERMANENT_STATUS = frozenset({400, 401, 402, 403, 404, 413, 422})


def status_of(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def classify_exception(exc: BaseException) -> str:
    """The detail of `provider_error:<detail>`: `timeout` or `unavailable`."""
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        return "timeout"
    return "unavailable"


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, SpendRefused):
        return False
    if classify_exception(exc) == "timeout":
        return True
    return status_of(exc) not in PERMANENT_STATUS


def invoke_with_retry(
    chat: ChatModel,
    messages: list[dict[str, Any]],
    tools: list[ToolSpec] | None,
    *,
    sleep: Callable[[float], None] | None = None,
    attempts: int = RETRY_ATTEMPTS,
    backoff: tuple[float, ...] = RETRY_BACKOFF_S,
) -> ChatReply:
    """Call the model, retrying a retryable failure. Raises `ProviderFailure` after the last attempt."""
    pause = sleep or (lambda _s: None)
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return chat.invoke(messages, tools)
        except ProviderFailure:
            raise
        except Exception as exc:  # mapped to a typed error below
            last = exc
            if not is_retryable(exc):
                break
            if attempt < attempts - 1:
                pause(backoff[min(attempt, len(backoff) - 1)])
    assert last is not None
    raise ProviderFailure(classify_exception(last)) from last
