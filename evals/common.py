"""Shared eval helpers. The only file under src/, evals/ and tests/ that names the gold file (SPEC 10.1).

- `load_gold()` returns `dev` rows by default. It returns `test` rows only when `MILO_FINAL=1`, and only
  `make final-test` sets that variable. Every `test` load appends a `started` line to
  `evals/results/test_access.log`.
- Evals refuse production settings (SPEC D76, D127).
- `gold_sha256()` lets F2 compare the gold file with `data/MANIFEST.json`.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from milo import settings

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
GOLD_PATH = DATA / "gold.jsonl"
MANIFEST_PATH = DATA / "MANIFEST.json"
TEST_ACCESS_LOG = ROOT / "evals" / "results" / "test_access.log"
SPLITS = ("dev", "test")

settings.assert_not_prod()


def final_mode() -> bool:
    return os.environ.get("MILO_FINAL") == "1"


def _log_test_access(status: str) -> None:
    TEST_ACCESS_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with TEST_ACCESS_LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {status}\n")


def log_test_status(status: str) -> None:
    """Record how a final run ended (`ok` or `failed`). F2 requires exactly one `ok` line."""
    _log_test_access(status)


def load_gold(split: str = "dev") -> list[dict[str, Any]]:
    """Gold rows of one split. `test` needs `MILO_FINAL=1` and is logged; anything else is refused."""
    settings.assert_not_prod()
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}, not {split!r}")
    if split == "test":
        if not final_mode():
            raise PermissionError(
                "the test split is sealed; only `make final-test` sets MILO_FINAL=1"
            )
        _log_test_access("started")
    rows = []
    with GOLD_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                if row.get("split") == split:
                    rows.append(row)
    return rows


def file_sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def gold_sha256() -> str:
    return file_sha256(GOLD_PATH)


def manifest_gold_sha256() -> str:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return str(manifest[f"data/{GOLD_PATH.name}"])


def gold_matches_manifest() -> bool:
    return gold_sha256() == manifest_gold_sha256()


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def provenance(
    *,
    model: str,
    seed: int,
    temperature: float,
    command: str,
    inputs: dict[str, Path | str],
) -> dict[str, Any]:
    """The provenance block every result file carries (SPEC D56). `inputs` maps a label to a file to hash."""
    return {
        "git_commit": git_commit(),
        "model": model,
        "seed": seed,
        "temperature": temperature,
        "command": command,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inputs": {label: file_sha256(path) for label, path in inputs.items()},
    }
