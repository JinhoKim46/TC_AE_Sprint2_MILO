"""Wire types shared by every module (SPEC 2A). They mirror `contract/fakes.py`; attribute names never change."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Risk = Literal["read", "write", "destructive"]
PolicyDecision = Literal["allowed", "needs_approval", "denied"]
Taint = Literal["low", "high"]


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
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    risk: Risk


@dataclass
class Document:
    doc_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


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
    policy_decision: str


@dataclass
class Response:
    answer: str
    sources: list[Source] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    cost_usd: float = 0.0
    stopped: bool = False
    error: str | None = None


@dataclass
class Event:
    type: str
    payload: dict[str, Any]
    ts: float


class ChatModel(Protocol):
    price_usd_per_mtok: tuple[float, float]

    def invoke(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec] | None = None
    ) -> ChatReply: ...


class Embeddings(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class ToolsBackend(Protocol):
    def specs(self) -> list[ToolSpec]: ...

    def call(self, name: str, args: dict[str, Any], *, user_id: str) -> Any: ...


class ApprovalStore(Protocol):
    """Approval lifecycle storage (SPEC 8). Contract mode: in memory. Discovery mode: the `approvals` table."""

    def create(
        self,
        *,
        scope: str,
        owner_id: str,
        turn_id: str,
        tool_name: str,
        args: dict[str, Any],
        context: Any = None,
    ) -> str: ...

    def get(self, approval_id: str, owner_id: str) -> dict[str, Any] | None: ...

    def set_status(self, approval_id: str, owner_id: str, status: str) -> None: ...
