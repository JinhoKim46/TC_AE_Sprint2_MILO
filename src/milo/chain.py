"""Discovery chain seam (SPEC section 5, M5b). S0 ships the seam only.

`validate -> extract -> merge -> protocol -> retrieve -> reply -> estimate`. The M5b owner replaces
`discovery_turn`. Until then, discovery mode fails loudly instead of answering.
"""

from __future__ import annotations

from typing import Any


def discovery_turn(*args: Any, **kwargs: Any) -> Any:
    raise NotImplementedError("the discovery chain lands in milestone M5b")
