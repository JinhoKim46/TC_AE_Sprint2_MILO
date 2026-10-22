"""Taint classification (SPEC D22, M4 body).

Customer text, uploads, retrieved KB text and tool output are untrusted. The pattern list fires on
instruction-like phrases only; plain imperatives such as "delete item 42" never fire it. The list is exactly the
seven phrases of D22; nothing is added.

Matching is done on a normalised copy of the text, so that cosmetic changes cannot hide a phrase: Unicode NFKC
(full-width letters), case folding, zero-width and other format characters removed, and any run of whitespace,
underscores or hyphens between words counts as one space (a phrase split over two lines still fires, threat-model
case A-02). A phrase must start at a word boundary, so "ecosystem note" is not "system note". The words after the
phrase are free ("ignore previous instructions" fires).
"""

from __future__ import annotations

import re
import unicodedata
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

_SEP = r"[\s_\-]+"
_PATTERN = re.compile(
    "(?<![a-z0-9])(?:"
    + "|".join(_SEP.join(re.escape(w) for w in p.split()) for p in INJECTION_PHRASES)
    + ")"
)


def normalise(text: str) -> str:
    """The form the pattern is matched against (casefolded, format characters dropped, NFKC)."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(c for c in folded if unicodedata.category(c) != "Cf")


def matched_phrases(untrusted_texts: Iterable[object]) -> list[str]:
    """The D22 phrases found in the texts, in list order, without duplicates. Empty means taint low."""
    found: set[str] = set()
    for item in untrusted_texts:
        text = normalise(item if isinstance(item, str) else str(item))
        for m in _PATTERN.finditer(text):
            squashed = re.sub(_SEP, " ", m.group(0))
            found.add(squashed)
    return [p for p in INJECTION_PHRASES if p in found]


def classify(untrusted_texts: Iterable[str]) -> Taint:
    """`high` when any untrusted text contains an injection phrase, else `low`."""
    for item in untrusted_texts:
        text = normalise(item if isinstance(item, str) else str(item))
        if _PATTERN.search(text):
            return "high"
    return "low"
