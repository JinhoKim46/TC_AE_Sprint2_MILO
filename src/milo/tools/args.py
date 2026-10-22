"""Argument hygiene for tool calls (SPEC D21, section 8).

Identity keys never come from the model. The stripping lives in `security.identity` (M4 decision); `clean_args`
stays here as the name the loop, `MiloBackend.call` and the approval service already call, and only delegates.
"""

from __future__ import annotations

from typing import Any

from milo.security.identity import clean_identity


def clean_args(raw: Any) -> Any:
    """Exact identity keys first, then look-alikes, at any depth. Returns a copy."""
    return clean_identity(raw)
