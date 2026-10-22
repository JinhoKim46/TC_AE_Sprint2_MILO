"""Query translation (SPEC D10): rewrite a question into one standalone search query.

The model step sits behind a callable, `Rewriter = Callable[[str], str]`. The live one (`chat_rewriter`) goes
through the chat model and `llm/retry.py`; tests and the dry harness pass a scripted function, so nothing here
calls a model by itself (SPEC D126). An empty or failed reply keeps the original question: a rewrite never
leaves the retriever without a query.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from milo.errors import ProviderFailure
from milo.llm.retry import invoke_with_retry
from milo.types import ChatModel

Rewriter = Callable[[str], str]

REWRITE_SYSTEM = (
    "You rewrite a customer's question into one standalone search query for a knowledge base of a services "
    "company (solutions, pricing, implementation, consultation, policies, cases). Keep every product name, "
    "number and constraint. Resolve pronouns. Use the words a help article would use. Reply with the query "
    "only: one line, no quotes, no explanation."
)


def ask(chat: ChatModel, system: str, user: str) -> str:
    """One chat call (through the retry site) with a system and a user message; returns the reply text."""
    reply = invoke_with_retry(
        chat,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        None,
        sleep=time.sleep,
    )
    return reply.content


def clean_rewrite(raw: str, original: str) -> str:
    """First non-empty line of the reply, without quotes or a leading label; the original when nothing is left."""
    for line in raw.strip().splitlines():
        text = line.strip().strip("\"'`").strip()
        if text.lower().startswith(("query:", "rewritten query:")):
            text = text.split(":", 1)[1].strip().strip("\"'`").strip()
        if text:
            return text
    return original


def chat_rewriter(chat: ChatModel) -> Rewriter:
    def rewrite(question: str) -> str:
        return ask(chat, REWRITE_SYSTEM, question)

    return rewrite


def rewrite_query(question: str, rewriter: Rewriter) -> tuple[str, str]:
    """(query to search with, raw reply). A provider failure keeps the original question (reply is empty)."""
    try:
        raw = rewriter(question)
    except ProviderFailure:
        return question, ""
    return clean_rewrite(raw, question), raw
