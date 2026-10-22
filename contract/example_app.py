"""A toy reference app that satisfies the contract. It is a stub for the contract, not a project to copy:
it has no real chunking, no query translation, no UI. Your project does the real work.

Mutations (env `EXAMPLE_APP_MUTATION`) break one behaviour each, so the kit can prove that the matching
test fails: no_policy, identity_from_args, leak_traceback, shared_threads.
"""
from __future__ import annotations

import math
import os
import re
from typing import Any

from fakes import (MAX_QUESTION_CHARS, ProviderError, Response, Source, ToolResult)

MIN_SCORE = 0.2
TOP_K = 3
MAX_STEPS = 3
RETRIES = 1


class App:
    def __init__(self, chat, embeddings, backend, mutation: str | None = None):
        self.chat, self.emb, self.backend = chat, embeddings, backend
        self.mut = mutation if mutation is not None else os.environ.get("EXAMPLE_APP_MUTATION")
        self.chunks: list[tuple[str, str, str, list[float]]] = []  # chunk_id, doc_id, text, vector
        self.threads: dict[str, list[dict[str, Any]]] = {}

    def ingest(self, docs) -> None:
        for d in docs:
            for i, para in enumerate(p.strip() for p in d.text.split("\n\n") if p.strip()):
                self.chunks.append((f"{d.doc_id}#{i}", d.doc_id, para, self.emb.embed([para])[0]))

    def _retrieve(self, q: str) -> list[Source]:
        qv = self.emb.embed([q])[0]
        scored = [Source(cid, did, sum(a * b for a, b in zip(qv, v)), text) for cid, did, text, v in self.chunks]
        scored = [s for s in scored if s.score >= MIN_SCORE]
        return sorted(scored, key=lambda s: -s.score)[:TOP_K]

    @staticmethod
    def _invalid(q: Any) -> str | None:
        if not isinstance(q, str) or not q.strip():
            return "empty"
        if len(q) > MAX_QUESTION_CHARS:
            return "too_long"
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", q):
            return "control_characters"
        return None

    def _invoke(self, messages, specs):
        last: Exception | None = None
        for _ in range(RETRIES + 1):
            try:
                return self.chat.invoke(messages, tools=specs)
            except (ProviderError, TimeoutError) as e:
                last = e
        raise last  # type: ignore[misc]

    def _cost(self, usage) -> float:
        pin, pout = self.chat.price_usd_per_mtok
        return (usage.input_tokens * pin + usage.output_tokens * pout) / 1e6

    def ask(self, question: str, user_id: str, thread_id: str) -> Response:
        bad = self._invalid(question)
        if bad:
            return Response("Sorry, I cannot process that input.", error=f"invalid_input:{bad}")
        sources = self._retrieve(question)
        if not sources:
            return Response("I do not have information about that.")
        key = thread_id if self.mut != "shared_threads" else "all"
        history = self.threads.setdefault(key, [])
        context = "\n".join(f"[{s.chunk_id}] {s.text}" for s in sources)
        messages = [{"role": "system", "content": "Answer only from the context. Cite chunk ids."}, *history,
                    {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"}]
        specs = self.backend.specs()
        risk = {s.name: s.risk for s in specs}
        results: list[ToolResult] = []
        cost, stopped, answer = 0.0, False, ""
        try:
            for _ in range(MAX_STEPS):
                reply = self._invoke(messages, specs)
                cost += self._cost(reply.usage)
                if not reply.tool_calls:
                    answer = reply.content
                    break
                for tc in reply.tool_calls:
                    args = dict(tc.args)
                    if tc.name not in risk:
                        results.append(ToolResult(tc.name, args, "unknown tool", False, "denied"))
                        continue
                    if risk[tc.name] == "destructive" and self.mut != "no_policy":
                        results.append(ToolResult(tc.name, args, None, False, "needs_approval"))
                        stopped, answer = True, f"I need your approval before running {tc.name}."
                        break
                    uid = args.get("user_id", user_id) if self.mut == "identity_from_args" else user_id
                    if self.mut != "identity_from_args":
                        args.pop("user_id", None)
                    try:
                        out, ok = self.backend.call(tc.name, args, user_id=uid), True
                    except Exception as e:  # tool failure goes back to the model as data
                        out, ok = f"tool error: {type(e).__name__}", False
                    results.append(ToolResult(tc.name, args, out, ok, "allowed"))
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(out)})
                if stopped:
                    break
            else:
                stopped, answer = True, "I stopped after too many steps."
        except (ProviderError, TimeoutError) as e:
            if self.mut == "leak_traceback":
                raise
            return Response("The assistant is temporarily unavailable. Please try again.",
                            sources=sources, tool_results=results, cost_usd=cost,
                            error=f"provider_error:{type(e).__name__.lower()}")
        history += [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
        return Response(answer, sources, results, cost, stopped, None)


def build_app(chat_model, embeddings, tools_backend) -> App:
    return App(chat_model, embeddings, tools_backend)
