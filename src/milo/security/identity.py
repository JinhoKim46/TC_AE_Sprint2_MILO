"""Identity binding (SPEC D21, section 8).

Identity keys are removed from model-supplied arguments before schema validation. The backend receives the
`user_id` that the session (web) or `ask` (contract) supplied, never a value from the arguments.

Two layers, both here (M4 decision, answering M3's question):
- `strip_identity` removes the four exact keys (`user_id`, `session_id`, `owner_id`, `thread_id`).
- `strip_lookalikes` also removes renamed forms that fold to the same key (`userId`, `USER_ID`, `owner-id`,
  full-width letters, padding), so a renamed key cannot slip past a schema that one day allows extra properties.
  Bare short names such as `uid` or `owner` are not identity keys: they are left in place and the closed schema
  rejects them (`additionalProperties: false`), which is the behaviour threat-model case A-07 expects.
`clean_identity` runs both and is what `tools/args.clean_args` delegates to. Inputs are never mutated.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

IDENTITY_KEYS = frozenset({"user_id", "session_id", "owner_id", "thread_id"})
_FOLDED_IDENTITY = frozenset(re.sub(r"[^a-z0-9]", "", k) for k in IDENTITY_KEYS)


def fold_key(key: object) -> str:
    """Case, width, punctuation and padding folded away: `User-ID `, `userId` and `ＵＳＥＲ＿ＩＤ` all give `userid`."""
    text = unicodedata.normalize("NFKC", str(key)).casefold()
    return re.sub(r"[^a-z0-9]", "", text)


def strip_identity(value: Any) -> Any:
    """Return a copy of `value` with every exact identity key removed, at any depth."""
    if isinstance(value, dict):
        return {
            k: strip_identity(v) for k, v in value.items() if k not in IDENTITY_KEYS
        }
    if isinstance(value, list):
        return [strip_identity(v) for v in value]
    return value


def strip_lookalikes(value: Any) -> Any:
    """Return a copy of `value` without any key that folds to an identity key, at any depth."""
    if isinstance(value, dict):
        return {
            k: strip_lookalikes(v)
            for k, v in value.items()
            if fold_key(k) not in _FOLDED_IDENTITY
        }
    if isinstance(value, list):
        return [strip_lookalikes(v) for v in value]
    return value


def clean_identity(raw: Any) -> Any:
    """Exact identity keys first, then look-alikes, at any depth. Run before schema validation (D21)."""
    return strip_lookalikes(strip_identity(raw))
