"""Generation step: (question, contexts, answer) from the product pipeline, through an injected callable.

`generate(rows, ask)` does not know whether `ask` is the live product (`app_ask` over `milo.app.build_app` with a
model from `milo.llm.cloud`) or the scripted fake of the dry mode. The cap is checked between questions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Generated:
    answer: str
    contexts: list[dict[str, Any]] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error: str | None = None


AskFn = Callable[[str], Generated]


def generate(
    sample: list[dict[str, Any]], ask: AskFn, max_cost: float | None = None
) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    spent = 0.0
    for q in sample:
        if max_cost is not None and spent >= max_cost:
            return (
                rows,
                True,
            )  # checked between questions; one question's calls can overshoot the cap
        g = ask(q["question"])
        spent += g.cost_usd
        rows.append(
            {
                "qid": q["qid"],
                "type": q["type"],
                "question": q["question"],
                "contexts": g.contexts,
                "answer": g.answer,
                "abstained": not g.contexts,
                "error": g.error,
                "tokens_in": g.tokens_in,
                "tokens_out": g.tokens_out,
                "cost_usd": round(g.cost_usd, 8),
            }
        )
    return rows, False


def app_ask(app: Any, user_id: str = "m7-eval") -> AskFn:
    """Adapt a `milo.app.App` (contract mode) to `AskFn`. One thread per question, so no history carries over."""
    counter = {"n": 0}

    def ask(question: str) -> Generated:
        counter["n"] += 1
        resp = app.ask(question, user_id, f"m7-{counter['n']}")
        contexts = [
            {
                "chunk_id": s.chunk_id,
                "doc_id": s.doc_id,
                "score": round(float(s.score), 6),
                "text": s.text,
            }
            for s in resp.sources
        ]
        return Generated(
            answer=resp.answer,
            contexts=contexts,
            cost_usd=float(resp.cost_usd),
            error=resp.error,
        )

    return ask


def scripted_ask(script: dict[str, dict[str, Any]]) -> AskFn:
    """Dry mode: answers and contexts keyed by the question text."""

    def ask(question: str) -> Generated:
        item = script[question]
        return Generated(answer=item["answer"], contexts=list(item["contexts"]))

    return ask
