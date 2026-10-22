"""Identity binding (SPEC D21, section 8).

Identity keys are removed from model-supplied arguments before schema validation. The backend receives the
`user_id` that the session (web) or `ask` (contract) supplied, never a value from the arguments.
"""

from __future__ import annotations

from typing import Any

IDENTITY_KEYS = frozenset({"user_id", "session_id", "owner_id", "thread_id"})


def strip_identity(value: Any) -> Any:
    """Return a copy of `value` with every identity key removed, at any depth."""
    if isinstance(value, dict):
        return {
            k: strip_identity(v) for k, v in value.items() if k not in IDENTITY_KEYS
        }
    if isinstance(value, list):
        return [strip_identity(v) for v in value]
    return value
