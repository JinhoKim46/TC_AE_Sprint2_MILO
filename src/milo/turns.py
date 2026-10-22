"""Turns: background runs and polled events (SPEC D47, D99 amended by D131, D132, D135).

`TurnRunner.submit` is what `POST /messages` (and `POST /upload` with a message) calls:

1. consent (D81, D135): no `interview` consent row, no turn (`PolicyDenied("consent_required")`, 403);
2. the input is validated (D23) and, for an upload, decoded and capped (D132);
3. per-account limits (D131): messages per minute through the rate-limit repository, the daily spend, and one
   running turn per account (`TurnRunning`, 409, enforced by the turns repository);
4. the turn row is created and the chain runs in the background (`spawn`, a thread by default), writing every
   event to `turn_events`.

The browser polls `events(user_id, turn_id, after=seq)` every 500 ms. Only the owner of a turn sees its events;
another owner gets `NotFound`. Tests inject `spawn=lambda fn: fn()` so a turn runs inline.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from milo import settings
from milo.auth.errors import RateLimited
from milo.auth.ratelimit import MESSAGES, UPLOADS, RateLimiter
from milo.chain import DiscoveryRuntime, runtime_of
from milo.errors import MiloError, PolicyDenied
from milo.facts.store import owner_hex
from milo.security.validate import validate_question, validate_upload

DAILY_SPEND_SCOPE = "daily_spend"
DAY_S = 86_400


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="milo-turn", daemon=True).start()


class TurnRunner:
    def __init__(
        self,
        app: Any,
        *,
        spawn: Callable[[Callable[[], None]], None] | None = None,
        clock: Callable[[], float] = time.time,
        require_consent: bool = True,
    ) -> None:
        self.app = app
        self.runtime: DiscoveryRuntime = runtime_of(app)
        self.repos = self.runtime.repos
        self.spawn = spawn or _thread
        self.clock = clock
        self.require_consent = require_consent
        self.limiter = RateLimiter(self.repos.rate_limits, clock)

    # ---- submitting a message ----

    def submit(
        self,
        user_id: str,
        session_id: str,
        text: str,
        *,
        upload: bytes | None = None,
        upload_mime: str = "text/plain",
    ) -> str:
        """Start a turn and return its id (32 hex). Raises a typed `MiloError` the web layer maps to a status."""
        owner = owner_hex(user_id)
        if self.require_consent and not self.repos.consents.has(owner, "interview"):
            raise PolicyDenied("consent_required")
        clean = validate_question(text)
        upload_text = None
        if upload is not None:
            upload_text = validate_upload(
                upload, upload_mime
            )  # a rejected file uses no slot
            self.limiter.hit(UPLOADS, owner)
        self.limiter.hit(MESSAGES, owner)
        if self.runtime.spent_today(owner) >= settings.ACCOUNT_DAILY_SPEND_USD:
            now = self.clock()
            raise RateLimited(
                DAILY_SPEND_SCOPE, DAY_S, max(1, int(DAY_S - now % DAY_S))
            )
        session = self.repos.sessions.get(owner, session_id)
        turn = self.repos.turns.start(owner, session["id"])
        turn_id = str(turn["id"])

        def work() -> None:
            try:
                self.runtime.run(
                    clean,
                    user_id,
                    session["id"],
                    turn_id=turn_id,
                    session_id=session["id"],
                    upload=upload_text,
                )
                status = "done"
            except Exception:  # the chain already maps errors; this is the last guard of a background thread
                status = "error"
                try:
                    self.repos.turns.add_event(
                        owner, turn_id, "error", {"code": "internal"}
                    )
                    self.repos.turns.add_event(
                        owner, turn_id, "done", {"error": "internal"}
                    )
                except MiloError:
                    pass
            self._close(owner, turn_id, status)

        self.spawn(work)
        return turn_id

    def _close(self, owner: str, turn_id: str, status: str) -> None:
        try:
            events = self.repos.turns.events(owner, turn_id)
            failed = any(e["type"] == "error" for e in events)
            self.repos.turns.finish(
                owner, turn_id, "error" if failed or status == "error" else "done"
            )
        except MiloError:
            pass

    # ---- polling ----

    def events(
        self, user_id: str, turn_id: str, after: int = 0
    ) -> list[dict[str, Any]]:
        """Events with `seq > after`, in order: `{seq, type, payload}`. Another owner's turn is `NotFound`."""
        owner = owner_hex(user_id)
        rows = self.repos.turns.events(owner, turn_id)
        return [
            {"seq": r["seq"], "type": r["type"], "payload": r["payload_json"]}
            for r in rows
            if int(r["seq"]) > int(after)
        ]

    def status(self, user_id: str, turn_id: str) -> str:
        return str(self.repos.turns.get(owner_hex(user_id), turn_id)["status"])

    # ---- approvals and taint ----

    def session_taint(self, user_id: str, session_id: str) -> str:
        """The stored taint of a session, as the approve-time policy reads it (D22, m4 request)."""
        return self.runtime.session_taint(owner_hex(user_id), session_id)

    def resolve_approval(self, user_id: str, approval_id: str, decision: str) -> Any:
        """`POST /approvals/<id>`: through `App.resume_approval`, which passes the session's stored taint."""
        return self.app.resume_approval(approval_id, decision, user_id)
