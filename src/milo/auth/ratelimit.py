"""Rate limits over the `rate_limits` repository (SPEC D92, D131, D142).

Counters are fixed windows kept in Postgres (or the in-memory equivalent), so limits hold across workers and
restarts. Emails and IPs are hashed before they become a key, so the table holds no address.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from milo import settings
from milo.auth.errors import RateLimited


@dataclass(frozen=True)
class Rule:
    scope: str
    limit: int
    window_s: int


# D92: five failures per fifteen minutes per account (the lockout), and the same five per address, so rotating emails from one address is stopped (SR A-17).
LOGIN_FAIL_ACCOUNT = Rule("login_fail_account", 5, 900)
LOGIN_FAIL_IP = Rule("login_fail_ip", 5, 900)
SIGNUP_IP = Rule("signup_ip", 10, 3600)
SIGNUP_EMAIL = Rule("signup_email", 3, 3600)
RESET_IP = Rule("reset_ip", 10, 3600)
RESET_EMAIL = Rule("reset_email", 3, 3600)
# D131, per account.
MESSAGES = Rule("messages", settings.ACCOUNT_MESSAGES_PER_MINUTE, 60)
UPLOADS = Rule("uploads", settings.ACCOUNT_UPLOADS_PER_HOUR, 3600)


def make_key(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:32]


class RateLimiter:
    def __init__(self, repo: Any, clock: Callable[[], float] = time.time) -> None:
        self.repo = repo
        self.clock = clock

    def _retry_after(self, rule: Rule, now: float) -> int:
        return max(1, int((now // rule.window_s + 1) * rule.window_s - now))

    def hit(self, rule: Rule, value: str) -> int:
        """Count one event; raise `RateLimited` when it is over the limit."""
        now = self.clock()
        count = int(
            self.repo.hit(rule.scope, make_key(value), window_s=rule.window_s, now=now)
        )
        if count > rule.limit:
            raise RateLimited(rule.scope, rule.window_s, self._retry_after(rule, now))
        return count

    def check(self, rule: Rule, value: str) -> None:
        """Raise `RateLimited` when the limit is already used up, without counting."""
        now = self.clock()
        count = int(
            self.repo.count(
                rule.scope, make_key(value), window_s=rule.window_s, now=now
            )
        )
        if count >= rule.limit:
            raise RateLimited(rule.scope, rule.window_s, self._retry_after(rule, now))

    def record(self, rule: Rule, value: str) -> int:
        """Count one event without raising (a failed sign-in)."""
        return int(
            self.repo.hit(
                rule.scope, make_key(value), window_s=rule.window_s, now=self.clock()
            )
        )

    def reset(self, rule: Rule, value: str) -> None:
        self.repo.reset(rule.scope, make_key(value))
