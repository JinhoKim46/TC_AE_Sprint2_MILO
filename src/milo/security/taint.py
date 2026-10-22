"""Taint classification (SPEC D22). S0 seam; the security milestone replaces the body.

Customer text, uploads, retrieved KB text and tool output are untrusted. The pattern list fires on
instruction-like phrases only; plain imperatives such as "delete item 42" never fire it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from milo.types import Taint

INJECTION_PHRASES = (
    "system override",
    "system note",
    "ignore previous",
    "ignore all previous",
    "mark every field",
    "set the price to zero",
    "set the quote to zero",
)

_PATTERN = re.compile("|".join(re.escape(p) for p in INJECTION_PHRASES), re.IGNORECASE)


def classify(untrusted_texts: Iterable[str]) -> Taint:
    """`high` when any untrusted text contains an injection phrase, else `low`."""
    for text in untrusted_texts:
        if _PATTERN.search(" ".join(text.split())):
            return "high"
    return "low"
