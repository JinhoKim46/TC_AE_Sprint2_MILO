"""Spend log and reserve check (SPEC D18). The budget is the owner's OpenRouter balance (`MILO_BUDGET_USD`).

Each paid call appends its actual cost to `logs/spend.jsonl`; a run that would take the balance below the
reserve is refused. Nothing here calls a model or the network.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from milo.errors import SpendRefused
from milo.settings import RESERVE_USD


class SpendLedger:
    def __init__(
        self,
        path: Path | str = "logs/spend.jsonl",
        budget_usd: float = 0.0,
        reserve_usd: float = RESERVE_USD,
    ):
        self.path = Path(path)
        self.budget_usd = budget_usd
        self.reserve_usd = reserve_usd

    def spent(self) -> float:
        if not self.path.exists():
            return 0.0
        total = 0.0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                total += float(json.loads(line).get("cost_usd", 0.0))
        return total

    def remaining(self) -> float:
        return self.budget_usd - self.spent()

    def check(self, planned_usd: float = 0.0) -> None:
        """Refuse when the planned spend would take the balance below the reserve."""
        if self.remaining() - planned_usd < self.reserve_usd:
            raise SpendRefused("budget below reserve")

    def record(self, cost_usd: float, *, model: str, purpose: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": time.time(),
            "model": model,
            "purpose": purpose,
            "cost_usd": cost_usd,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
