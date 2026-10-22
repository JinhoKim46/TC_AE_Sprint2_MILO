"""The discovery chain (SPEC section 5, D2, D37, D39, D108, D131 to D134, D141; M5b body).

Per owner message, an LCEL sequence of seven nodes over one `TurnState`:

    policy -> extract -> merge -> protocol -> retrieve -> reply -> estimate

- policy: taint of the owner text and any upload (D22), sticky per session; the owner message is saved as sent.
- extract: model call 1, a JSON object validated by Pydantic; one JSON re-ask (D23b). If both fail to parse, the
  turn answers with the "repeat the last point" reply and makes no further call.
- merge: code (`facts.merge`): verbatim evidence (D134), type, range, unit and cross-field checks, statuses.
- protocol: the engine's state, estimate status and next question (from the YAML).
- retrieve: only when the extraction's `asks_about` is module, process or price.
- reply: the tool loop with at most `4 - calls used` model calls and never more than two (one reply, one tool
  follow-up), under the same policy as contract mode; then the D108 reply check in code.
- estimate: when the protocol allows a figure, `pricing.estimator` runs in code and its figures go to the
  `estimate_updated` event only, never to the model.

A discovery turn makes at most four model calls. Events (`node_started`, `node_finished`, `fact_recorded`,
`fact_rejected`, `document_truncated`, `source`, `policy`, `approval_required`, `estimate_updated`, `answer`,
`error`, `done`) go to `on_event` and to the `turn_events` table. Traces carry hashes, lengths, counts and
cost, never owner text (D141).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from milo.errors import (
    NOT_FOUND,
    MiloError,
    NotFound,
    PolicyDenied,
    ProviderFailure,
    format_error,
    user_message,
)
from milo.facts.merge import MergeResult
from milo.facts.model import Extraction, Proposal, parse_extraction
from milo.facts.store import SessionFacts, TurnBinding, owner_hex
from milo.llm.client import JSON_REASK, parse_json_object
from milo.llm.retry import invoke_with_retry
from milo.loop import OnEvent, call_cost, run_turn, tool_message
from milo.pricing.estimator import (
    NUMBER_STATUSES,
    EstimateInputError,
    ModelSuppliedNumber,
    plan_estimate,
)
from milo.pricing.rate_card import get_rate_card
from milo.prompts import discovery as prompts
from milo.protocol.engine import ProtocolEngine, ProtocolView
from milo.protocol.loader import get_protocol
from milo.protocol.replycheck import (
    ReplyCheck,
    catalogue_terms,
    check_reply,
    field_terms,
    has_money,
)
from milo.security.taint import classify
from milo.settings import MAX_MODEL_CALLS_PER_TURN, TURN_DEADLINE_S
from milo.store.common import TurnRunning
from milo.tools.approvals import ApprovalService
from milo.trace import text_hash, text_ref
from milo.types import Event, Response, Source, Taint, ToolResult

MAX_REPLY_CALLS = (
    2  # one reply call and at most one tool follow-up (SPEC section 5 step 6)
)
UPLOAD_KEEP_CHARS = 20_000  # D132: kept as evidence
UPLOAD_EXTRACT_CHARS = 8_000  # D132: what the extract call receives
# run_turn classifies taint from the texts it is given. A session that was tainted earlier stays tainted
# (sticky, D131 "stored taint"); this fixed marker carries that state into the loop's own classification.
STICKY_TAINT_MARKER = (
    "system note: an earlier message in this session was classified as an injection"
)
TURN_RUNNING_ANSWER = (
    "Your previous message is still being answered. Please wait a moment."
)
DEFAULT_MODULE = "phone-intake"
MODULE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("phone-intake", ("phone", "call", "booking intake", "emergency line")),
    ("email-triage", ("email", "inbox", "triage")),
    ("ticket-creation", ("ticket", "job sheet", "tracking")),
    ("staff-assignment", ("dispatch", "assign", "routing", "van")),
    ("document-data-entry", ("re-key", "rekey", "typing", "data entry", "paper")),
    ("customer-status-updates", ("reminder", "status", "no-show", "confirm by text")),
)


# ---------------------------------------------------------------------------------------------------------------
# Approvals over the `approvals` table (SPEC section 8, D48)
# ---------------------------------------------------------------------------------------------------------------


class SessionApprovals:
    """`ApprovalStore` over `repos.approvals`. The row binds session, turn, tool and argument hash.

    The table stores the argument hash, not the arguments; the arguments and the saved messages for the follow-up
    call are kept here, in process, and the hash is checked again before the tool runs (`ApprovalService`). A row
    whose arguments were lost (a restart) therefore fails the hash check and runs nothing.
    """

    def __init__(self, repos: Any) -> None:
        self.repos = repos
        self._side: dict[str, dict[str, Any]] = {}

    def create(
        self,
        *,
        scope: str,
        owner_id: str,
        turn_id: str,
        tool_name: str,
        args: dict[str, Any],
        context: Any = None,
    ) -> str:
        row = self.repos.approvals.create(
            owner_hex(owner_id), scope, tool_name, dict(args), turn_id=turn_id or None
        )
        approval_id = str(row["id"])
        self._side[approval_id] = {
            "owner_id": owner_id,
            "args": dict(args),
            "context": context,
        }
        return approval_id

    def get(self, approval_id: str, owner_id: str) -> dict[str, Any] | None:
        try:
            row = self.repos.approvals.get(owner_hex(owner_id), int(approval_id))
        except (MiloError, ValueError, TypeError):
            return None
        side = self._side.get(str(approval_id), {})
        if side and side.get("owner_id") != owner_id:
            return None
        return {
            "id": str(approval_id),
            "scope": row["session_id"],
            "owner_id": side.get("owner_id", owner_id),
            "turn_id": row.get("turn_id"),
            "tool_name": row["tool_name"],
            "args": dict(side.get("args", {})),
            "args_hash": row["args_hash"],
            "status": row["status"],
            "expires_at": row["expires_at"],
            "context": side.get("context"),
        }

    def set_status(self, approval_id: str, owner_id: str, status: str) -> None:
        owner = owner_hex(owner_id)
        # The table has no `denied_at_approve`; nothing ran, so the row is closed as `kept` (or `expired` once it
        # was claimed as `approved`, the only closing status the table allows from there).
        targets = ["kept", "expired"] if status == "denied_at_approve" else [status]
        for to in targets:
            try:
                self.repos.approvals.transition(owner, int(approval_id), to)
                return
            except PolicyDenied:
                continue
            except (MiloError, ValueError) as exc:
                raise NotFound("approval") from exc
        raise NotFound("approval")


# ---------------------------------------------------------------------------------------------------------------
# Turn state
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class TurnState:
    text: str
    user_id: str
    thread_id: str
    owner: str
    session_id: str
    turn_id: str
    on_event: OnEvent | None
    deadline: float
    upload: str | None = None
    taint: Taint = "low"
    message_id: int | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    view: ProtocolView | None = None
    confirming: str | None = None
    last_question: str | None = None
    extraction: Extraction | None = None
    merged: MergeResult | None = None
    sources: list[Source] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    answer: str = ""
    stopped: bool = False
    error: str | None = None
    calls: int = 0
    cost: float = 0.0
    check: ReplyCheck | None = None
    estimate: dict[str, Any] | None = None
    done: bool = False


def default_modules(facts: dict[str, dict[str, Any]]) -> list[str]:
    """Module ids for the panel estimate when the model named none: keyword match over the owner's goals and
    channels. Provisional; the report milestone (M5c) owns the recommendation."""
    words: list[str] = []
    for fid in ("automation_goals", "request_channels", "pain_points"):
        value = facts.get(fid, {}).get("value")
        if isinstance(value, list):
            words += [str(v).lower() for v in value]
    text = " | ".join(words)
    picked = [m for m, keys in MODULE_KEYWORDS if any(k in text for k in keys)]
    return picked[:6] or [DEFAULT_MODULE]


# ---------------------------------------------------------------------------------------------------------------
# The runtime: one per discovery App
# ---------------------------------------------------------------------------------------------------------------


class DiscoveryRuntime:
    def __init__(
        self,
        app: Any,
        repos: Any,
        *,
        session_facts: SessionFacts | None = None,
        protocol: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.app = app
        self.repos = repos
        self.protocol = protocol or get_protocol()
        self.engine = ProtocolEngine(self.protocol)
        self.facts = session_facts or SessionFacts(repos, self.protocol)
        self.approvals = SessionApprovals(repos)
        self.rate_card = get_rate_card()
        self.scope_terms = [*catalogue_terms(), *field_terms(self.protocol)]
        self.clock = clock
        self._default_session: dict[str, str] = {}
        self._tainted: set[tuple[str, str]] = set()
        self._spend: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()
        self._pipeline: Any = None

    # ---- identity, sessions, taint, spend ----

    def ensure_owner(self, owner: str) -> None:
        try:
            self.repos.profiles.get(owner)
        except NotFound:
            create = getattr(self.repos.profiles, "create_confirmed", None)
            if create is not None:
                create(owner)

    def session_for(self, owner: str, thread_id: str) -> str:
        """A thread id that names one of the owner's sessions opens it (web, D97); any other thread id maps to the
        owner's default session, so one owner has one session in `build_discovery_app` (D2)."""
        try:
            return str(self.repos.sessions.get(owner, thread_id)["id"])
        except MiloError:
            pass
        with self._lock:
            sid = self._default_session.get(owner)
            if sid is None:
                sid = str(self.repos.sessions.create(owner)["id"])
                self._default_session[owner] = sid
            return sid

    def session_taint(self, owner: str, session_id: str) -> Taint:
        """Sticky: high once any owner message, upload, retrieved text or tool output of the session was."""
        if (owner, session_id) in self._tainted:
            return "high"
        try:
            texts = [
                m["content"]
                for m in self.repos.messages.history(owner, session_id)
                if m.get("role") == "owner"
            ]
        except MiloError:
            return "low"
        if classify(texts) == "high":
            self._tainted.add((owner, session_id))
            return "high"
        return "low"

    def mark_tainted(self, owner: str, session_id: str) -> None:
        self._tainted.add((owner, session_id))

    def _day(self) -> str:
        return datetime.fromtimestamp(self.clock(), tz=UTC).strftime("%Y-%m-%d")

    def record_spend(self, owner: str, cost: float) -> None:
        key = (owner, self._day())
        with self._lock:
            self._spend[key] = self._spend.get(key, 0.0) + cost

    def spent_today(self, owner: str) -> float:
        return self._spend.get((owner, self._day()), 0.0)

    # ---- events and traces ----

    def emit(self, st: TurnState, type_: str, **payload: Any) -> None:
        clean = json.loads(json.dumps(payload, default=str))
        if st.on_event is not None:
            try:
                st.on_event(Event(type_, clean, time.time()))
            except Exception:  # an event consumer never breaks a turn
                pass
        try:
            self.repos.turns.add_event(st.owner, st.turn_id, type_, clean)
        except MiloError:
            pass

    def trace(
        self,
        st: TurnState,
        node: str,
        *,
        duration_ms: float = 0.0,
        status: str = "ok",
        error_code: str | None = None,
        **detail: Any,
    ) -> None:
        self.app.tracer.record(
            node,
            thread_id=st.session_id,
            turn_id=st.turn_id,
            duration_ms=duration_ms,
            status=status,
            error_code=error_code,
            **detail,
        )
        try:
            self.repos.traces.record(
                st.owner,
                st.turn_id,
                node,
                duration_ms=duration_ms,
                status=status,
                error_code=error_code,
                detail=detail,
            )
        except MiloError:
            pass

    # ---- the pipeline ----

    def _nodes(self) -> list[tuple[str, Callable[[TurnState], None]]]:
        return [
            ("policy", self._policy),
            ("extract", self._extract),
            ("merge", self._merge),
            ("protocol", self._protocol),
            ("retrieve", self._retrieve),
            ("reply", self._reply),
            ("estimate", self._estimate),
        ]

    def _wrap(
        self, name: str, fn: Callable[[TurnState], None]
    ) -> Callable[[TurnState], TurnState]:
        def node(st: TurnState) -> TurnState:
            if st.done:
                return st
            self.emit(st, "node_started", node=name)
            started = time.monotonic()
            fn(st)
            ms = (time.monotonic() - started) * 1000
            self.emit(st, "node_finished", node=name, duration_ms=round(ms, 1))
            return st

        return node

    def pipeline(self) -> Any:
        """The LCEL sequence of the seven nodes (langchain-core, imported here so `import milo.app` stays light)."""
        if self._pipeline is None:
            from langchain_core.runnables import RunnableLambda

            nodes = [RunnableLambda(self._wrap(n, f)) for n, f in self._nodes()]
            seq: Any = nodes[0]
            for nxt in nodes[1:]:
                seq = seq | nxt
            self._pipeline = seq
        return self._pipeline

    def run(
        self,
        text: str,
        user_id: str,
        thread_id: str,
        on_event: OnEvent | None = None,
        *,
        turn_id: str | None = None,
        session_id: str | None = None,
        upload: str | None = None,
    ) -> Response:
        owner = owner_hex(user_id)
        self.ensure_owner(owner)
        sid = session_id or self.session_for(owner, thread_id)
        own_turn = turn_id is None
        if own_turn:
            try:
                turn_id = str(self.repos.turns.start(owner, sid)["id"])
            except TurnRunning:
                error = format_error("policy_denied", "turn_running")
                return Response(answer=TURN_RUNNING_ANSWER, error=error)
        assert turn_id is not None
        deadline_s = getattr(self.app, "deadline_s", TURN_DEADLINE_S)
        st = TurnState(
            text=text,
            user_id=user_id,
            thread_id=thread_id,
            owner=owner,
            session_id=sid,
            turn_id=turn_id,
            on_event=on_event,
            deadline=time.monotonic() + deadline_s,
            upload=upload,
        )
        try:
            self.pipeline().invoke(st)
        except (
            Exception
        ):  # a defect never reaches the owner as a traceback (SPEC section 9)
            st.error = st.error or format_error("provider_error", "unavailable")
            st.answer = user_message(st.error)
            self.trace(st, "chain", status="error", error_code="internal")
        return self._finish(st, own_turn)

    def _finish(self, st: TurnState, own_turn: bool) -> Response:
        self.record_spend(st.owner, st.cost)
        if st.error is None and st.answer:
            try:
                self.repos.messages.add(
                    st.owner,
                    st.session_id,
                    "assistant",
                    st.answer,
                    trace_hash=text_hash(st.answer),
                )
            except MiloError:
                pass
            self.emit(st, "answer", text=st.answer, stopped=st.stopped)
        if st.error is not None:
            self.emit(st, "error", code=st.error, message=st.answer)
        view = st.view.payload() if st.view is not None else None
        self.emit(
            st,
            "done",
            view=view,
            cost_usd=st.cost,
            model_calls=st.calls,
            taint=st.taint,
            reply_check=list(st.check.verdicts) if st.check else [],
            error=st.error,
        )
        self.trace(
            st,
            "turn",
            status="error" if st.error else "ok",
            error_code=st.error,
            model_calls=st.calls,
            cost_usd=st.cost,
            taint=st.taint,
        )
        if own_turn:
            try:
                self.repos.turns.finish(
                    st.owner, st.turn_id, "error" if st.error else "done"
                )
            except MiloError:
                pass
        return Response(
            answer=st.answer,
            sources=st.sources,
            tool_results=st.tool_results,
            cost_usd=st.cost,
            stopped=st.stopped,
            error=st.error,
        )

    # ---- nodes ----

    def _policy(self, st: TurnState) -> None:
        try:
            st.history = self.repos.messages.history(st.owner, st.session_id)
        except MiloError:
            st.history = []
        kept = st.upload
        if kept is not None and len(kept) > UPLOAD_KEEP_CHARS:
            self.emit(
                st,
                "document_truncated",
                kept_chars=UPLOAD_KEEP_CHARS,
                dropped_chars=len(kept) - UPLOAD_KEEP_CHARS,
            )
            kept = kept[:UPLOAD_KEEP_CHARS]
        st.upload = kept
        turn_taint = classify([t for t in (st.text, kept) if t])
        sticky = self.session_taint(st.owner, st.session_id)
        st.taint = "high" if "high" in (turn_taint, sticky) else "low"
        if st.taint == "high":
            self.mark_tainted(st.owner, st.session_id)
        msg = self.repos.messages.add(
            st.owner, st.session_id, "owner", st.text, trace_hash=text_hash(st.text)
        )
        st.message_id = int(msg["id"])
        if not st.history:
            try:
                self.repos.sessions.rename(st.owner, st.session_id, st.text[:60])
            except MiloError:
                pass
        self.emit(st, "policy", stage="input", taint=st.taint)
        self.trace(
            st,
            "policy_input",
            taint=st.taint,
            input=text_ref(st.text),
            upload_chars=len(kept or ""),
        )
        st.facts = self.facts.load(st.owner, st.session_id)
        before = self.engine.evaluate(st.facts)
        st.confirming = self.engine.open_conflict(st.facts)
        st.last_question = before.next_question

    def _call(self, st: TurnState, messages: list[dict[str, Any]], node: str) -> Any:
        started = time.monotonic()
        reply = invoke_with_retry(self.app.chat, messages, None, sleep=self.app.sleep)
        st.calls += 1
        cost = call_cost(reply, self.app.chat.price_usd_per_mtok)
        st.cost += cost
        self.trace(
            st,
            node,
            duration_ms=(time.monotonic() - started) * 1000,
            input_tokens=reply.usage.input_tokens,
            output_tokens=reply.usage.output_tokens,
            cost_usd=cost,
        )
        return reply

    def _extract(self, st: TurnState) -> None:
        excerpt = st.upload[:UPLOAD_EXTRACT_CHARS] if st.upload else None
        messages = prompts.extract_messages(
            self.protocol,
            st.history,
            st.text,
            last_question=st.last_question,
            document=excerpt,
        )
        try:
            reply = self._call(st, messages, "extract")
            parsed = parse_json_object(reply.content)
            extraction = parse_extraction(parsed) if parsed is not None else None
            if extraction is None:
                again = [
                    *messages,
                    {"role": "assistant", "content": reply.content},
                    {"role": "user", "content": JSON_REASK},
                ]
                reply = self._call(st, again, "extract_reask")
                parsed = parse_json_object(reply.content)
                extraction = parse_extraction(parsed) if parsed is not None else None
        except ProviderFailure as exc:
            self._fail(st, exc.error)
            return
        if extraction is None:
            st.answer = prompts.REPEAT_ANSWER
            st.view = self.engine.evaluate(st.facts)
            st.done = True
            self.trace(
                st, "extract_unparsed", status="error", error_code="unparsed_json"
            )
            return
        st.extraction = extraction

    def _fail(self, st: TurnState, error: str) -> None:
        st.error = error
        st.answer = user_message(error)
        st.view = self.engine.evaluate(st.facts)
        st.done = True

    def _merge(self, st: TurnState) -> None:
        assert st.extraction is not None
        proposals = [
            Proposal(f.field_id, f.value, f.unit, f.period, f.evidence_text or "")
            for f in st.extraction.facts
        ]
        sources = [st.text] + ([st.upload] if st.upload else [])
        result = self.facts.merge_and_store(
            st.owner,
            st.session_id,
            proposals,
            sources=sources,
            taint=st.taint,
            confirming=st.confirming,
            message_id=st.message_id,
            upload=st.upload,
        )
        st.merged = result
        self._emit_merge(st, result)
        st.facts = result.facts
        self.trace(
            st,
            "merge",
            proposed=len(proposals),
            invalid_items=st.extraction.invalid_items,
            recorded=len(result.changes),
            rejected=len(result.rejections),
            unknown_ids=sum(
                1 for r in result.rejections if r.reason == "unknown_field"
            ),
        )

    def _emit_merge(self, st: TurnState, result: MergeResult) -> None:
        for ch in result.changes:
            self.emit(st, "fact_recorded", **ch.event_payload())
        for rj in result.rejections:
            self.emit(st, "fact_rejected", **rj.event_payload())

    def _protocol(self, st: TurnState) -> None:
        st.view = self.engine.evaluate(st.facts)
        self.trace(
            st,
            "protocol",
            protocol_state=st.view.state,
            next_question_id=st.view.next_question_id,
            missing=len(st.view.missing),
            conflicts=len(st.view.conflicts),
        )

    def _retrieve(self, st: TurnState) -> None:
        if st.extraction is None or not st.extraction.retrieve:
            return
        try:
            st.sources = list(self.app.retriever.search(st.text))
        except Exception:  # an embedding failure must not end the interview; the reply goes on without excerpts
            st.sources = []
            self.trace(st, "retrieve", status="error", error_code="provider_error")
            return
        for s in st.sources:
            self.emit(
                st,
                "source",
                chunk_id=s.chunk_id,
                doc_id=s.doc_id,
                score=round(s.score, 4),
            )
        if st.sources and classify([s.text for s in st.sources]) == "high":
            st.taint = "high"
            self.mark_tainted(st.owner, st.session_id)
        self.trace(
            st, "retrieve", hits=len(st.sources), asks_about=st.extraction.asks_about
        )

    def _reply(self, st: TurnState) -> None:
        assert st.view is not None
        budget = min(MAX_REPLY_CALLS, MAX_MODEL_CALLS_PER_TURN - st.calls)
        if budget <= 0:
            st.answer = st.view.next_question or "Thank you."
            return
        messages = prompts.reply_messages(
            self.protocol,
            st.view,
            st.facts,
            st.history,
            st.text,
            sources=st.sources,
            extra=self.app.system_prompt_extra,
            document_attached=st.upload is not None,
        )
        untrusted = [st.text, *(s.text for s in st.sources)]
        if st.upload:
            untrusted.append(st.upload)
        if st.taint == "high":
            untrusted.append(STICKY_TAINT_MARKER)
        binding = TurnBinding(
            owner=st.owner,
            session_id=st.session_id,
            sources=[st.text] + ([st.upload] if st.upload else []),
            taint=st.taint,
            message_id=st.message_id,
            confirming=st.confirming,
            upload=st.upload,
        )

        def relay(ev: Event) -> None:
            if ev.type == "tool_result":
                self.emit(
                    st,
                    "policy",
                    stage="tool",
                    tool=ev.payload.get("name"),
                    decision=ev.payload.get("decision"),
                    ok=ev.payload.get("ok"),
                )

        self.facts.bind(st.user_id, binding)
        try:
            result = run_turn(
                chat=self.app.chat,
                tools=self.app.tools,
                messages=messages,
                user_id=st.user_id,
                thread_id=st.session_id,
                turn_id=st.turn_id,
                untrusted_texts=untrusted,
                approvals=self.approvals,
                approval_scope=st.session_id,
                tracer=self.app.tracer,
                sleep=self.app.sleep,
                deadline_s=max(0.0, st.deadline - time.monotonic()),
                max_calls=budget,
                on_event=relay,
            )
        finally:
            self.facts.release(st.user_id)
        st.calls += result.model_calls
        st.cost += result.cost_usd
        st.tool_results = result.tool_results
        for ch in binding.changes:
            self.emit(st, "fact_recorded", **ch.event_payload())
        for rj in binding.rejections:
            self.emit(st, "fact_rejected", **rj.event_payload())
        if binding.changes:
            st.facts = self.facts.load(st.owner, st.session_id)
            st.view = self.engine.evaluate(st.facts)
        if any(tr.ok and tr.output is not None for tr in result.tool_results):
            outputs = [json.dumps(tr.output, default=str) for tr in result.tool_results]
            if classify(outputs) == "high":
                self.mark_tainted(st.owner, st.session_id)
        if result.error is not None:
            st.error = result.error
            st.answer = result.answer
            st.done = True
            return
        if result.approval_id:
            st.stopped = True
            st.answer = result.answer
            name = result.tool_results[-1].name if result.tool_results else ""
            self.emit(
                st, "approval_required", approval_id=result.approval_id, tool=name
            )
            return
        st.check = check_reply(
            result.answer,
            st.view,
            scope_terms=self.scope_terms,
            doc_ids=[s.doc_id for s in st.sources],
        )
        st.answer = st.check.text
        self.trace(st, "reply_check", verdicts=list(st.check.verdicts))

    def _estimate(self, st: TurnState) -> None:
        view = st.view
        if view is None or view.estimate_status is None:
            return
        if not view.number_allowed:
            self.emit(
                st,
                "estimate_updated",
                status=view.estimate_status,
                protocol_estimate_status=view.estimate_status,
                assumptions=[f"conflict:{c}" for c in view.conflicts],
                monthly=None,
                setup=None,
            )
            return
        chosen = [
            tr.arguments.get("module_ids")
            for tr in st.tool_results
            if tr.name == "estimate_cost" and tr.ok
        ]
        modules = (
            list(chosen[-1])
            if chosen and isinstance(chosen[-1], list)
            else default_modules(st.facts)
        )
        try:
            est = plan_estimate(
                self.rate_card,
                self.protocol,
                self.facts.as_list(st.owner, st.session_id),
                {"module_ids": modules},
            )
        except (EstimateInputError, ModelSuppliedNumber):
            self.emit(
                st,
                "estimate_updated",
                status="unavailable",
                protocol_estimate_status=view.estimate_status,
                monthly=None,
                setup=None,
            )
            return
        payload = (
            est.event_payload() if est.status in NUMBER_STATUSES else est.tool_output()
        )
        payload["protocol_estimate_status"] = view.estimate_status
        payload["protocol_assumptions"] = list(view.assumptions)
        st.estimate = payload
        self.emit(st, "estimate_updated", **payload)
        self.trace(st, "estimate", estimate_status=est.status, modules=len(modules))

    # ---- approvals ----

    def resume_approval(
        self, approval_id: str, decision: str, user_id: str
    ) -> Response:
        row = self.approvals.get(approval_id, user_id)
        if row is None:
            return Response(answer=user_message(NOT_FOUND), error=NOT_FOUND)
        if decision not in ("approve", "keep"):
            bad = format_error("invalid_input")
            return Response(answer=user_message(bad), error=bad)
        owner = owner_hex(user_id)
        session_id = str(row["scope"])
        taint = self.session_taint(owner, session_id)
        outcome = ApprovalService(self.app.tools, self.approvals).resolve(
            approval_id, decision, user_id, taint=taint
        )
        fixed: dict[str, tuple[str, str | None]] = {
            "not_found": (user_message(NOT_FOUND), NOT_FOUND),
            "expired": ("This request expired. Nothing was changed.", None),
            "already_resolved": (
                "This request was already handled. Nothing more was changed.",
                None,
            ),
            "kept": (
                "Understood. I kept things as they are. Nothing was changed.",
                None,
            ),
            "denied": (
                "This request can no longer be carried out. Nothing was changed.",
                None,
            ),
            "denied_at_approve": (
                "This request can no longer be carried out. Nothing was changed.",
                None,
            ),
        }
        if outcome.status in fixed:
            fixed_answer, fixed_error = fixed[outcome.status]
            return Response(answer=fixed_answer, error=fixed_error)
        name, args = row["tool_name"], row["args"]
        tool_result = ToolResult(name, args, outcome.output, outcome.ok, "allowed")
        ctx = row.get("context") or {}
        saved = list(ctx.get("messages") or [])
        if saved and saved[0].get("role") == "system":
            saved[0] = {
                "role": "system",
                "content": f"{saved[0]['content']}\n\n{prompts.followup_instruction()}",
            }
        messages = [
            *saved,
            tool_message(ctx.get("call_id", "approved"), name, outcome.output),
        ]
        try:
            reply = invoke_with_retry(
                self.app.chat, messages, None, sleep=self.app.sleep
            )
        except ProviderFailure as exc:
            return Response(
                answer=user_message(exc.error),
                tool_results=[tool_result],
                error=exc.error,
            )
        cost = call_cost(reply, self.app.chat.price_usd_per_mtok)
        self.record_spend(owner, cost)
        answer = reply.content.strip() or "Done."
        if has_money(answer):
            answer = "Done. The estimate panel shows the current figures."
        try:
            self.repos.messages.add(
                owner, session_id, "assistant", answer, trace_hash=text_hash(answer)
            )
        except MiloError:
            pass
        return Response(answer=answer, tool_results=[tool_result], cost_usd=cost)


# ---------------------------------------------------------------------------------------------------------------
# Entry points used by `milo.app` (the discovery branch)
# ---------------------------------------------------------------------------------------------------------------


def attach(
    app: Any, repos: Any = None, *, session_facts: SessionFacts | None = None
) -> DiscoveryRuntime:
    """Give a discovery `App` its runtime. Without repositories, an in-memory store is used (SPEC D70)."""
    if repos is None:
        from milo.store import memory_repositories

        repos = memory_repositories()
    runtime = DiscoveryRuntime(app, repos, session_facts=session_facts)
    app.discovery_runtime = runtime
    return runtime


def runtime_of(app: Any) -> DiscoveryRuntime:
    rt = getattr(app, "discovery_runtime", None)
    return rt if isinstance(rt, DiscoveryRuntime) else attach(app)


def discovery_turn(
    app: Any,
    text: str,
    user_id: str,
    thread_id: str,
    on_event: OnEvent | None = None,
    **kwargs: Any,
) -> Response:
    """One discovery turn for a validated owner message (called by `App.ask` in discovery mode)."""
    return runtime_of(app).run(text, user_id, thread_id, on_event, **kwargs)


def resume_approval(
    app: Any, approval_id: str, decision: str, user_id: str
) -> Response:
    return runtime_of(app).resume_approval(approval_id, decision, user_id)


def answer_has_figure(answer: str) -> bool:
    """For the evaluation: a money amount in an answer the owner sees (the same rule the reply check enforces)."""
    return has_money(answer)


__all__ = [
    "DiscoveryRuntime",
    "SessionApprovals",
    "TurnState",
    "attach",
    "default_modules",
    "discovery_turn",
    "resume_approval",
    "runtime_of",
]
