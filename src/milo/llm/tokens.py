"""Token counting without a tokenizer download (offline, deterministic).

A model-independent estimate of about four characters per token. Billing uses the provider's usage field
(`cloud.py`); this count is only for budgeting prompts and planning spend before a call.
"""

from __future__ import annotations

import math
from typing import Any

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4


def count_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def count_message_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(
        count_tokens(str(m.get("content") or "")) + MESSAGE_OVERHEAD_TOKENS
        for m in messages
    )
