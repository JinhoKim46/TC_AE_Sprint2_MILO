"""Thinking-output hygiene (SPEC D23b): strip `<think>...</think>` blocks before any parsing."""

from __future__ import annotations

import re

_BLOCK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_OPEN_TAIL = re.compile(r"<think\b[^>]*>.*\Z", re.DOTALL | re.IGNORECASE)
_ORPHAN_CLOSE = re.compile(r"\A.*?</think\s*>", re.DOTALL | re.IGNORECASE)


def strip_think(text: str) -> str:
    """Remove closed think blocks, an unterminated trailing block, and a stray leading `</think>`.

    A reply that is only thinking becomes the empty string (the caller then falls back to a safe reply).
    """
    if not text:
        return ""
    out = _BLOCK.sub("", text)
    out = _OPEN_TAIL.sub("", out)
    if re.search(r"</think\s*>", out, re.IGNORECASE):
        out = _ORPHAN_CLOSE.sub("", out, count=1)
    return out.strip()
