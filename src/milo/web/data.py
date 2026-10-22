"""The web layer's data adapter over the repositories, the discovery runtime and the turn runner (SPEC 16, D79,
D97 to D102, D131, D135, D144).

One class, used the same way by `create_app` (Postgres repositories) and by the tests, the dev factory and
`evals/ui/record.py` (the in-memory store). Every owner call takes the signed-in user id (32 hex) and goes through
the repositories' owner filter (D91); another owner's id is `NotFound` and the routes answer 404 `not_found`.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

from milo.auth.errors import RateLimited
from milo.chain import DiscoveryRuntime, default_modules, runtime_of
from milo.errors import NOT_FOUND, InvalidInput, MiloError, NotFound, PolicyDenied
from milo.facts.store import owner_hex
from milo.pricing.estimator import (
    NUMBER_STATUSES,
    EstimateInputError,
    ModelSuppliedNumber,
    plan_estimate,
)
from milo.store.common import TurnRunning
from milo.tools.approvals import ApprovalService
from milo.turns import TurnRunner

ASSESSED = ("reported", "confirmed", "estimated")
SUBMITTABLE = ("assessment_ready", "quote_ready")
NOTE_MAX = 500
TOOL_RESULT_MAX = 600
RUNNING_SCOPE = "running_turns"
RUNNING_WINDOW_S = (
    120  # the turn deadline (SPEC D2); a retry after it finds the turn closed
)


class TurnBusy(MiloError):
    """A turn of this same session is still running (SPEC D47, D99): 409 `turn_running`."""

    code = "turn_running"


def label(field_id: str) -> str:
    return field_id.replace("_", " ").capitalize()


def shown(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(shown(v) for v in value)
    return str(value)


class TrackedTurns:
    """Proxy over the turns repository that remembers each account's running turn in this process.

    The repository enforces one running turn per account (D131) but does not say which turn runs; the web layer
    needs that to answer 409 for the same session and 429 for another session (m5b finding 6), and to file the
    `tool_call` events of the drawer under the running turn.
    """

    def __init__(self, inner: Any, running: dict[str, tuple[str, str]]) -> None:
        self._inner = inner
        self._running = running

    def start(self, owner_id: str, session_id: str) -> dict[str, Any]:
        row: dict[str, Any] = self._inner.start(owner_id, session_id)
        self._running[owner_hex(owner_id)] = (str(row["session_id"]), str(row["id"]))
        return row

    def finish(self, owner_id: str, turn_id: str, status: str = "done") -> Any:
        out = self._inner.finish(owner_id, turn_id, status)
        owner = owner_hex(owner_id)
        if self._running.get(owner, ("", ""))[1] == str(turn_id):
            self._running.pop(owner, None)
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class EventedTools:
    """Proxy over the tools backend: every executed call adds a `tool_call` event (name, arguments, result) to the
    caller's running turn, for the reply details drawer (SPEC 16). Policy decisions arrive as `policy` events."""

    def __init__(
        self, inner: Any, repos: Any, running: dict[str, tuple[str, str]]
    ) -> None:
        self._inner = inner
        self._repos = repos
        self._running = running

    def specs(self) -> Any:
        return self._inner.specs()

    def call(self, name: str, args: dict[str, Any], *, user_id: str) -> Any:
        ok = True
        result: Any = None
        try:
            result = self._inner.call(name, args, user_id=user_id)
            return result
        except Exception:
            ok, result = False, {"error": "tool_failed"}
            raise
        finally:
            self._event(user_id, name, args, result, ok)

    def _event(self, user_id: str, name: str, args: Any, result: Any, ok: bool) -> None:
        owner = owner_hex(user_id)
        running = self._running.get(owner)
        if running is None:
            return
        text = json.dumps(result, default=str)
        try:
            self._repos.turns.add_event(
                owner,
                running[1],
                "tool_call",
                {
                    "name": name,
                    "args": json.loads(json.dumps(args, default=str)),
                    "ok": ok,
                    "result": text[:TOOL_RESULT_MAX],
                },
            )
        except MiloError:
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class WebData:
    def __init__(
        self,
        app: Any,
        accounts: Any,
        *,
        spawn: Callable[[Callable[[], None]], None] | None = None,
        report_builder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.app = app
        self.accounts = accounts
        self.runtime: DiscoveryRuntime = runtime_of(app)
        self.repos = self.runtime.repos
        self.protocol = self.runtime.protocol
        self.engine = self.runtime.engine
        self.running: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()
        if not isinstance(self.repos.turns, TrackedTurns):
            self.repos.turns = TrackedTurns(self.repos.turns, self.running)
        if not isinstance(app.tools, EventedTools):
            app.tools = EventedTools(app.tools, self.repos, self.running)
        self.runner = TurnRunner(app, spawn=spawn)
        self.report_builder = report_builder

    # ---- accounts and consent ----

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        try:
            p = self.repos.profiles.get(user_id)
        except (NotFound, PolicyDenied):
            return None
        name_of = getattr(self.accounts, "first_name", None)
        first = (name_of(user_id) if name_of else None) or p.get("display_name")
        email = p.get("email") or ""
        return {
            "id": user_id,
            "email": email,
            "first_name": first or email.split("@")[0] or "there",
            "role": p.get("role", "owner"),
        }

    def has_consent(self, user_id: str) -> bool:
        return bool(self.repos.consents.has(user_id, "interview"))

    def record_consent(self, user_id: str, version: str) -> None:
        self.repos.consents.record(user_id, "interview", version)

    def consents(self, user_id: str) -> list[dict[str, Any]]:
        return list(self.repos.consents.history(user_id))

    def profile(self, user_id: str) -> dict[str, Any]:
        return {
            "customer": self.repos.customers.get(user_id) or {},
            "deletion_requests": self.repos.deletion_requests.history(user_id),
            "field_requests": self.repos.field_requests.history(user_id),
        }

    def deletion_request(self, user_id: str, reason: str) -> None:
        self.repos.deletion_requests.add(user_id, reason[:NOTE_MAX] or None)

    # ---- sessions (D97 to D102) ----

    def list_sessions(
        self, user_id: str, *, query: str = "", archived: bool = False
    ) -> list[dict[str, Any]]:
        rows = self.repos.sessions.search(
            user_id, status="archived" if archived else None, query=query or None
        )
        return [self._session(r) for r in rows]

    @staticmethod
    def _session(row: dict[str, Any]) -> dict[str, Any]:
        return {**row, "title": row.get("title") or "New diagnosis"}

    def get_session(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        try:
            return self._session(self.repos.sessions.get(user_id, session_id))
        except NotFound:
            return None

    def create_session(self, user_id: str) -> str:
        self.runtime.ensure_owner(owner_hex(user_id))
        return str(self.repos.sessions.create(user_id)["id"])

    def rename_session(
        self, user_id: str, session_id: str, title: str
    ) -> dict[str, Any]:
        if not isinstance(title, str) or not title.strip():
            raise InvalidInput("title")
        return self._session(self.repos.sessions.rename(user_id, session_id, title))

    def archive_session(self, user_id: str, session_id: str) -> dict[str, Any]:
        return self._session(self.repos.sessions.archive(user_id, session_id))

    def restore_session(self, user_id: str, session_id: str) -> dict[str, Any]:
        return self._session(self.repos.sessions.restore(user_id, session_id))

    def list_messages(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        try:
            rows = self.repos.messages.history(user_id, session_id)
        except NotFound:
            return []
        roles = {"owner": "user", "assistant": "assistant"}
        return [
            {"role": roles[r["role"]], "text": r["content"]}
            for r in rows
            if r.get("role") in roles
        ]

    # ---- the specification and the estimate ----

    def get_spec(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        try:
            facts = self.runtime.facts.load(owner_hex(user_id), session_id)
        except NotFound:
            return None
        view = self.engine.evaluate(facts)
        fields = []
        assessed = total = 0
        for f in self.protocol.fields:
            if not self.engine.applies(f, facts):
                continue
            fact = facts.get(f.id)
            status = str(fact["status"]) if fact else "missing"
            if "assessment" in f.required_for:
                total += 1
                assessed += status in ASSESSED
            fields.append(
                {
                    "id": f.id,
                    "label": label(f.id),
                    "value": ""
                    if fact is None or status == "conflicting"
                    else shown(fact.get("value")),
                    "status": status,
                    "quote": (fact or {}).get("evidence_text") or "",
                    "recorded": fact is not None,
                }
            )
        return {
            "state": view.state,
            "progress": assessed,
            "total": total,
            "fields": fields,
            "missing": [label(m) for m in view.missing],
            "to_confirm": [label(c) for c in view.to_confirm],
            "estimate": self._estimate(user_id, session_id, facts, view),
            "can_submit": view.state in SUBMITTABLE,
        }

    def _estimate(
        self, user_id: str, session_id: str, facts: Any, view: Any
    ) -> dict[str, Any] | None:
        """The panel estimate, computed in code from the stored facts (never from the model, SPEC 5 step 7)."""
        if view.estimate_status is None:
            return None
        if not view.number_allowed:
            return {"status": view.estimate_status, "monthly": None, "setup": None}
        try:
            est = plan_estimate(
                self.runtime.rate_card,
                self.protocol,
                self.runtime.facts.as_list(owner_hex(user_id), session_id),
                {"module_ids": default_modules(facts)},
            )
        except (EstimateInputError, ModelSuppliedNumber):
            return {"status": "unavailable", "monthly": None, "setup": None}
        if est.status not in NUMBER_STATUSES:
            return {"status": est.status, "monthly": None, "setup": None}
        return est.event_payload()

    def field_request(
        self, user_id: str, field_id: str, note: str, session_id: str | None
    ) -> None:
        if field_id not in {f.id for f in self.protocol.fields}:
            raise NotFound()
        if session_id:
            self.repos.sessions.get(user_id, session_id)
        self.repos.field_requests.add(
            user_id,
            field_id,
            note=note.strip()[:NOTE_MAX] or None,
            session_id=session_id or None,
        )

    # ---- turns (D47, D99, D131) ----

    def send(
        self, user_id: str, session_id: str, text: str, *, upload: bytes | None = None
    ) -> str:
        """Start a turn. Same session busy: `TurnBusy` (409). Another session of the account busy: 429."""
        owner = owner_hex(user_id)
        self.runtime.ensure_owner(owner)
        try:
            return self.runner.submit(
                user_id, session_id, text, upload=upload, upload_mime="text/plain"
            )
        except TurnRunning:
            busy = self.running.get(owner)
            if busy is not None and busy[0] == str(session_id).replace("-", ""):
                raise TurnBusy() from None
            raise RateLimited(
                RUNNING_SCOPE, RUNNING_WINDOW_S, RUNNING_WINDOW_S
            ) from None

    def events(self, user_id: str, turn_id: str, after: int) -> list[dict[str, Any]]:
        return self.runner.events(user_id, turn_id, after)

    def resolve_approval(
        self, user_id: str, approval_id: str, decision: str
    ) -> dict[str, Any]:
        if decision not in ("approve", "keep"):
            raise InvalidInput("decision")
        resp = self.runner.resolve_approval(user_id, approval_id, decision)
        if resp.error == NOT_FOUND:
            raise NotFound()
        return {
            "answer": resp.answer,
            "error": resp.error,
            "tool_calls": [
                {
                    "name": t.name,
                    "args": t.arguments,
                    "ok": t.ok,
                    "decision": t.policy_decision,
                }
                for t in resp.tool_results
            ],
        }

    def withdraw(self, user_id: str, submission_id: str) -> str:
        """The owner's withdraw button (SPEC 8 step 5): the same policy through `ApprovalService`, no model call."""
        try:
            number = int(submission_id)
        except ValueError:
            raise NotFound() from None
        sub = self.repos.submissions.get(user_id, number)
        service = ApprovalService(self.app.tools, self.runtime.approvals)
        outcome = service.withdraw(
            number, user_id=user_id, scope=str(sub["session_id"])
        )
        if outcome.status == "not_found":
            raise NotFound()
        return outcome.status

    # ---- submit and reports (D40, D41) ----

    def submit(self, user_id: str, session_id: str) -> int:
        owner = owner_hex(user_id)
        self.repos.sessions.get(user_id, session_id)
        facts = self.runtime.facts.as_list(owner, session_id)
        view = self.engine.evaluate({f["field_id"]: f for f in facts})
        if view.state not in SUBMITTABLE:
            raise PolicyDenied("not_ready")
        from milo.report import ReportError, attach_fields, build_report, make_snapshot

        keep = ("field_id", "value", "unit", "period", "status", "evidence_text")
        snapshot = make_snapshot(
            self.protocol, [{k: f.get(k) for k in keep} for f in facts]
        )
        sub = self.repos.submissions.create(user_id, session_id, snapshot)
        sid = int(sub["id"])
        try:
            report = (self.report_builder or build_report)(snapshot)
            self.repos.submissions.attach_report(
                user_id, sid, report_json=report, **attach_fields(report)
            )
        except (ReportError, KeyError, StopIteration):
            self.repos.submissions.set_status(user_id, sid, "failed")
        return sid

    def _all_sessions(self, user_id: str) -> list[dict[str, Any]]:
        return [
            *self.repos.sessions.search(user_id),
            *self.repos.sessions.search(user_id, status="archived"),
        ]

    def list_reports(self, user_id: str) -> list[dict[str, Any]]:
        out = []
        for s in self._all_sessions(user_id):
            for sub in self.repos.submissions.for_session(user_id, s["id"]):
                out.append(self._report_row(sub, s.get("title")))
        return sorted(out, key=lambda r: -int(r["id"]))

    @staticmethod
    def _report_row(sub: dict[str, Any], title: str | None) -> dict[str, Any]:
        return {
            "id": sub["id"],
            "title": f"{title or 'Diagnosis'} (revision {sub['revision']})",
            "created_at": sub["submitted_at"],
            "status": sub["status"],
            "session_id": sub["session_id"],
            "json": sub.get("report_json"),
        }

    def get_report(self, user_id: str, report_id: str) -> dict[str, Any] | None:
        try:
            sub = self.repos.submissions.get(user_id, int(report_id))
            session = self.repos.sessions.get(user_id, sub["session_id"])
        except (NotFound, ValueError):
            return None
        return self._report_row(sub, session.get("title"))

    # ---- operator (D130, D144) ----

    def is_operator(self, user_id: str) -> bool:
        try:
            return str(self.repos.profiles.role(user_id)) == "operator"
        except (NotFound, PolicyDenied):
            return False

    def ops_submissions(self) -> list[dict[str, Any]]:
        return list(self.repos.operator.submissions())

    def ops_submission(self, submission_id: str) -> dict[str, Any] | None:
        try:
            return dict(self.repos.operator.submission(int(submission_id)))
        except (NotFound, ValueError):
            return None

    def ops_audit(self) -> list[dict[str, Any]]:
        return list(self.repos.audit.recent())

    def ops_delete_customer(self, actor_id: str, customer_id: str) -> None:
        try:
            self.repos.operator.delete_customer(actor_id, customer_id)
        except PolicyDenied:
            raise NotFound() from None

    def recent_traces(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self.app.recent_traces(limit))
