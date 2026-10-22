"""Rate-limit counters in Postgres (SPEC D142). Keyed by scope and key, not by owner; the app role may count."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from milo.store.db import Database


def window_start(now: float, window_s: int) -> datetime:
    return datetime.fromtimestamp(int(now // window_s) * window_s, tz=UTC)


class PgRateLimits:
    def __init__(self, db: Database) -> None:
        self.db = db

    def hit(
        self, scope: str, key: str, *, window_s: int, now: float | None = None
    ) -> int:
        """Count one event in the current fixed window and return the window's count."""
        t = time.time() if now is None else now
        row = self.db.one(
            None,
            "insert into rate_limits (scope, key, window_start, count) values (%s, %s, %s, 1) "
            "on conflict (scope, key, window_start) do update set count = rate_limits.count + 1 "
            "returning count",
            (scope, key, window_start(t, window_s)),
        )
        return int(row["count"]) if row else 0

    def count(
        self, scope: str, key: str, *, window_s: int, now: float | None = None
    ) -> int:
        t = time.time() if now is None else now
        row: dict[str, Any] | None = self.db.one(
            None,
            "select count from rate_limits where scope = %s and key = %s and window_start = %s",
            (scope, key, window_start(t, window_s)),
        )
        return int(row["count"]) if row else 0

    def reset(self, scope: str, key: str) -> None:
        self.db.run(
            None, "delete from rate_limits where scope = %s and key = %s", (scope, key)
        )

    def prune(self, older_than_s: float = 86400.0, now: float | None = None) -> int:
        t = time.time() if now is None else now
        return self.db.run(
            None,
            "delete from rate_limits where window_start < %s",
            (datetime.fromtimestamp(t - older_than_s, tz=UTC),),
        )
