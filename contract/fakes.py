"""Self-contained fakes and data types for the contract tests. Standard library only.

Copy the whole `contract/` folder into your project repo; nothing here imports from the course repo.
The types below are the shapes the contract tests read. Your app may return its own classes as long as
the attribute names match (the tests use attribute access only).
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

# ---- contract limits (tests and example app share them) ----
MAX_QUESTION_CHARS = 4000

# ---- shapes the app exchanges with its collaborators ----


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ChatReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


@dataclass
class Document:
    doc_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema of the arguments, never including user_id
    risk: str  # "read" | "write" | "destructive"


# ---- shapes the app returns ----


@dataclass
class Source:
    chunk_id: str
    doc_id: str
    score: float
    text: str = ""


@dataclass
class ToolResult:
    name: str
    arguments: dict[str, Any]
    output: Any
    ok: bool
    policy_decision: str  # "allowed" | "needs_approval" | "denied"


@dataclass
class Response:
    answer: str
    sources: list[Source] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    cost_usd: float = 0.0
    stopped: bool = False
    error: str | None = None


# ---- fakes ----


class ProviderError(Exception):
    """What a model provider failure looks like to the app (rate limit, timeout, 5xx)."""


class ScriptedChat:
    """A chat model that replays a script. No network.

    `invoke(messages, tools=None) -> ChatReply`. Each script item is a ChatReply, an Exception (raised), or a
    callable `(messages) -> ChatReply`. When the script is exhausted it returns `default`.
    `calls` counts invocations; `seen` keeps the messages of every call.
    `price_usd_per_mtok = (input, output)` lets the cost test check `Response.cost_usd`.
    """

    def __init__(self, script: list[Any] | None = None, default: str = "ok",
                 price_usd_per_mtok: tuple[float, float] = (1.0, 2.0)):
        self.script = list(script or [])
        self.default = default
        self.price_usd_per_mtok = price_usd_per_mtok
        self.calls = 0
        self.seen: list[list[dict[str, Any]]] = []
        self.seen_tools: list[Any] = []

    def invoke(self, messages: list[dict[str, Any]], tools: list[ToolSpec] | None = None) -> ChatReply:
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        self.seen_tools.append(tools)
        if not self.script:
            return ChatReply(content=self.default, usage=Usage(10, 5))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(messages)
        return item


_TOKEN = re.compile(r"\w+")


class HashEmbedder:
    """A deterministic bag-of-words embedder: hashed unigrams, L2-normalised. No model, no network."""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in _TOKEN.findall(text.lower()):
            h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:4], "big")
            v[h % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v] if n else v


@dataclass
class ExecutedCall:
    name: str
    args: dict[str, Any]
    user_id: str


class FakeBackend:
    """The tools backend. The app must discover tools through `specs()` and run them through `call()`.

    Tools: `lookup_item` (read), `create_note` (write), `delete_item` (destructive). Every executed call is
    recorded in `executed`, with the identity the app passed as `user_id`.
    """

    def __init__(self, handlers: dict[str, Callable[..., Any]] | None = None):
        self.executed: list[ExecutedCall] = []
        self._handlers = handlers or {}

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec("lookup_item", "Look up one item by id.",
                     {"type": "object", "properties": {"item_id": {"type": "string"}},
                      "required": ["item_id"], "additionalProperties": False}, "read"),
            ToolSpec("create_note", "Save a short note for the user.",
                     {"type": "object", "properties": {"text": {"type": "string"}},
                      "required": ["text"], "additionalProperties": False}, "write"),
            ToolSpec("delete_item", "Delete one item by id. Cannot be undone.",
                     {"type": "object", "properties": {"item_id": {"type": "string"}},
                      "required": ["item_id"], "additionalProperties": False}, "destructive"),
        ]

    def call(self, name: str, args: dict[str, Any], *, user_id: str) -> Any:
        self.executed.append(ExecutedCall(name, dict(args), user_id))
        if name in self._handlers:
            return self._handlers[name](args, user_id)
        return {"ok": True, "tool": name, "user": user_id}
