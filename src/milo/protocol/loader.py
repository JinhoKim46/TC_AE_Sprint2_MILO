"""The committed protocol file, loaded once per path (SPEC 5.1, D36).

`schema.py` (S0) is the Pydantic model; this module only resolves the path against the repository root, so the
chain, the tools backend and the evaluation all read the same file whatever the working directory is.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from milo.protocol.schema import Protocol, load_protocol

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROTOCOL_PATH = REPO_ROOT / "protocols" / "discovery.v1.yaml"


@lru_cache(maxsize=8)
def _cached(path: str) -> Protocol:
    return load_protocol(path)


def get_protocol(path: Path | str | None = None) -> Protocol:
    """The validated protocol. The default is `protocols/discovery.v1.yaml` in this repository."""
    target = Path(path) if path is not None else DEFAULT_PROTOCOL_PATH
    return _cached(str(target.resolve()))
