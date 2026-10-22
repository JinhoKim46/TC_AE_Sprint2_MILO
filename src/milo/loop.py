"""The tool loop (SPEC section 5, D2; M3 body, `run_turn` unchanged from the S0 seam).

At most `max_calls` model calls per turn in total. Tools come from `tools.specs()` and run only through
`tools.call(name, args, user_id=...)`. Order of every attempt (SPEC section 8): strip identity keys and
look-alikes, validate the arguments against the tool's schema, ask the policy (risk times taint, with the
backend's pricing fields), then run, or create an approval and stop. Every attempt lands in `tool_results` and in
the trace (argument hash and length only, D141). Tool output is untrusted: it is reclassified after each call.
The loop never retries a model call itself (`llm/retry.py` does) and never raises: failures become a
`provider_error` result, and a policy or classifier fault fails closed as `denied`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from milo.errors import ProviderFailure, format_error, user_message
from milo.llm.retry import invoke_with_retry
from milo.security.policy import Decision, decide
from milo.security.taint import classify
from milo.settings import MAX_MODEL_CALLS_PER_TURN, TURN_DEADLINE_S
from milo.tools.args import clean_args
from milo.trace import Tracer, text_ref
from milo.types import (
    ApprovalStore,
    ChatModel,
    ChatReply,
    Event,
    Taint,
    ToolResult,
    ToolSpec,
    ToolsBackend,
)

MAX_TOOL_OUTPUT_CHARS = 6000
NEEDS_APPROVAL_ANSWER = (
    "That action needs your approval before I run it. Nothing has been changed. "
    "Please approve it or keep things as they are."
)
NO_ANSWER = "I could not put an answer together. Please try asking again."

OnEvent = Callable[[Event], None]


@dataclass
class TurnResult:
    answer: str
    tool_results: list[ToolResult] = field(default_factory=list)
    cost_usd: float = 0.0
    stopped: bool = False
    error: str | None = None
    model_calls: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)
    approval_id: str | None = None


def call_cost(reply: ChatReply, price: tuple[float, float]) -> float:
    """SPEC section 9: (input_tokens x price_in + output_tokens x price_out) / 1_000_000."""
    return (
        reply.usage.input_tokens * price[0] + reply.usage.output_tokens * price[1]
    ) / 1_000_000


def emit(on_event: OnEvent | None, type_: str, **payload: Any) -> None:
    if on_event is not None:
        on_event(Event(type_, payload, time.time()))


def tool_message(call_id: str, name: str, output: Any) -> dict[str, Any]:
    text = json.dumps(output, default=str)
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": text[:MAX_TOOL_OUTPUT_CHARS],
    }


def run_tool(
    spec: ToolSpec | None,
    name: str,
    raw_args: dict[str, Any],
    *,
    tools: ToolsBackend,
    user_id: str,
    taint: Taint,
    approvals: ApprovalStore,
    approval_scope: str,
    turn_id: str,
    tracer: Tracer,
    thread_id: str,
    context: Callable[[], Any],
) -> tuple[ToolResult, str | None]:
    """One tool attempt. Returns the result and, for a destructive call, the id of the pending approval."""
    if not isinstance(raw_args, dict):  # a model can emit anything as arguments
        raw_args = {}
    args = clean_args(raw_args)  # before schema validation (SPEC D21)
    if spec is None:
        tracer.record(
            "policy",
            thread_id=thread_id,
            turn_id=turn_id,
            tool=name,
            decision="denied",
            reason="unknown tool",
        )
        return ToolResult(name, args, {"error": "unknown_tool"}, False, "denied"), None
    errors = sorted(
        jsonschema.Draft202012Validator(spec.parameters).iter_errors(args),
        key=lambda e: list(e.path),
    )
    if errors:
        tracer.record(
            "policy",
            thread_id=thread_id,
            turn_id=turn_id,
            tool=name,
            decision="denied",
            reason="invalid arguments",
        )
        return ToolResult(
            name,
            args,
            {"error": "invalid_arguments", "detail": errors[0].message},
            False,
            "denied",
        ), None
    try:
        decision = decide(
            spec, args, taint, frozenset(getattr(tools, "pricing_fields", ()))
        )
    except Exception:  # fail closed: a policy fault never lets a call through
        decision = Decision("denied", "policy error")
    tracer.record(
        "policy",
        thread_id=thread_id,
        turn_id=turn_id,
        tool=name,
        risk=spec.risk,
        taint=taint,
        decision=decision.decision,
        reason=decision.reason,
        args=text_ref(json.dumps(args, sort_keys=True, default=str)),
    )
    if decision.decision == "denied":
        return ToolResult(name, args, {"error": "policy_denied"}, False, "denied"), None
    if decision.decision == "needs_approval":
        approval_id = approvals.create(
            scope=approval_scope,
            owner_id=user_id,
            turn_id=turn_id,
            tool_name=name,
            args=args,
            context=context(),
        )
        output = {"status": "needs_approval", "approval_id": approval_id}
        return ToolResult(name, args, output, False, "needs_approval"), approval_id
    started = time.monotonic()
    try:
        output = tools.call(name, args, user_id=user_id)
        ok = True
    except Exception:  # a tool failure goes back to the model as a result, never to the user (SPEC D23)
        output, ok = {"error": "tool_failed"}, False
    tracer.record(
        "tool",
        thread_id=thread_id,
        turn_id=turn_id,
        duration_ms=(time.monotonic() - started) * 1000,
        status="ok" if ok else "error",
        tool=name,
    )
    return ToolResult(name, args, output, ok, "allowed"), None


def run_turn(
    *,
    chat: ChatModel,
    tools: ToolsBackend,
    messages: list[dict[str, Any]],
    user_id: str,
    thread_id: str,
    turn_id: str,
    untrusted_texts: list[str],
    approvals: ApprovalStore,
    approval_scope: str,
    tracer: Tracer,
    sleep: Callable[[float], None] | None = None,
    deadline_s: float = TURN_DEADLINE_S,
    clock: Callable[[], float] = time.monotonic,
    max_calls: int = MAX_MODEL_CALLS_PER_TURN,
    on_event: OnEvent | None = None,
) -> TurnResult:
    specs = {s.name: s for s in tools.specs()}
    offered = list(specs.values())
    taint: Taint = classify(untrusted_texts)
    deadline = clock() + deadline_s
    msgs = list(messages)
    result = TurnResult(answer="")
    while True:
        if clock() > deadline:
            return _failed(result, msgs, format_error("provider_error", "timeout"))
        offer = offered if result.model_calls < max_calls - 1 else None
        started = time.monotonic()
        try:
            reply = invoke_with_retry(chat, msgs, offer, sleep=sleep)
        except ProviderFailure as exc:
            tracer.record(
                "model_call",
                thread_id=thread_id,
                turn_id=turn_id,
                status="error",
                error_code=exc.error,
            )
            return _failed(result, msgs, exc.error)
        result.model_calls += 1
        result.cost_usd += call_cost(reply, chat.price_usd_per_mtok)
        tracer.record(
            "model_call",
            thread_id=thread_id,
            turn_id=turn_id,
            duration_ms=(time.monotonic() - started) * 1000,
            input_tokens=reply.usage.input_tokens,
            output_tokens=reply.usage.output_tokens,
            tool_calls=len(reply.tool_calls),
        )
        emit(on_event, "model_call", n=result.model_calls)
        if not reply.tool_calls or offer is None:
            result.answer = reply.content.strip() or NO_ANSWER
            result.messages = msgs
            return result
        msgs.append(
            {
                "role": "assistant",
                "content": reply.content,
                "tool_calls": [
                    {"id": c.id, "name": c.name, "args": c.args}
                    for c in reply.tool_calls
                ],
            }
        )
        for i, call in enumerate(reply.tool_calls):
            tr, approval_id = run_tool(
                specs.get(call.name),
                call.name,
                call.args,
                tools=tools,
                user_id=user_id,
                taint=taint,
                approvals=approvals,
                approval_scope=approval_scope,
                turn_id=turn_id,
                tracer=tracer,
                thread_id=thread_id,
                context=lambda: {"messages": list(msgs), "call_id": call.id},
            )
            result.tool_results.append(tr)
            emit(
                on_event,
                "tool_result",
                name=tr.name,
                decision=tr.policy_decision,
                ok=tr.ok,
            )
            if approval_id is not None:
                for rest in reply.tool_calls[i + 1 :]:
                    msgs.append(tool_message(rest.id, rest.name, {"status": "skipped"}))
                result.answer = NEEDS_APPROVAL_ANSWER
                result.stopped = True
                result.approval_id = approval_id
                result.messages = msgs
                return result
            msgs.append(tool_message(call.id, call.name, tr.output))
            taint = (
                classify([*untrusted_texts, json.dumps(tr.output, default=str)])
                if taint == "low"
                else taint
            )


def _failed(result: TurnResult, msgs: list[dict[str, Any]], error: str) -> TurnResult:
    result.error = error
    result.answer = user_message(error)
    result.messages = msgs
    return result
