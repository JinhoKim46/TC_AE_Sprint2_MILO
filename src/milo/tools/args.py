"""Argument hygiene for tool calls (SPEC D21, section 8).

Identity keys never come from the model. `security.identity.strip_identity` removes the four exact keys; this
module adds the look-alikes (`userId`, `USER_ID`, `owner-id`, full-width letters, padding) so that a renamed key
cannot slip past a schema that one day allows extra properties. The loop and `MiloBackend.call` both use it, and
both run it before schema validation.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from milo.security.identity import IDENTITY_KEYS, strip_identity

_FOLDED_IDENTITY = frozenset(re.sub(r"[^a-z0-9]", "", k) for k in IDENTITY_KEYS)


def _fold(key: object) -> str:
    text = unicodedata.normalize("NFKC", str(key)).casefold()
    return re.sub(r"[^a-z0-9]", "", text)


def _strip_lookalikes(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _strip_lookalikes(v)
            for k, v in value.items()
            if _fold(k) not in _FOLDED_IDENTITY
        }
    if isinstance(value, list):
        return [_strip_lookalikes(v) for v in value]
    return value


def clean_args(raw: Any) -> Any:
    """Exact identity keys first (the S0 seam), then look-alikes, at any depth. Returns a copy."""
    return _strip_lookalikes(strip_identity(raw))
