"""Self-query (SPEC D11, D151): the model turns a question into metadata filters, with a required fallback.

The model returns JSON `{section?, product?, updated_after?}`. `parse_filters` validates it with Pydantic (extra
keys refused) and, when a vocabulary of the knowledge base is given, refuses a value that is not in it. A failed
parse, an extractor error or a filtered search that comes back empty falls back to the unfiltered search, so a
bad filter can cost precision but never leaves the retriever empty (`self_query_rank`). The model step is a
callable (`FilterExtractor`), scripted in tests and the dry harness (SPEC D126).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from milo.errors import ProviderFailure
from milo.rag.translate import ask
from milo.types import ChatModel

FilterExtractor = Callable[[str], str]
T = TypeVar("T")

SELFQUERY_SYSTEM = (
    "You extract search filters from a customer's question about a services company's knowledge base. Reply "
    'with one JSON object and nothing else. Keys, all optional: "section" (one of: {sections}), "product" '
    '(one of: {products}), "updated_after" (a date YYYY-MM-DD, only when the question asks for newer '
    "content). Include a key only when the question clearly names it; when unsure, leave it out. If no filter "
    "applies, reply {{}}."
)


class SelfQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: str | None = None
    product: str | None = None
    updated_after: str | None = None

    @field_validator("updated_after")
    @classmethod
    def _date(cls, v: str | None) -> str | None:
        if v is not None:
            date.fromisoformat(v)  # ValueError becomes a validation error
        return v


@dataclass(frozen=True)
class Vocabulary:
    sections: frozenset[str]
    products: frozenset[str]

    @classmethod
    def from_metadata(cls, metas: Iterable[dict[str, Any]]) -> Vocabulary:
        ms = list(metas)
        return cls(
            frozenset(str(m["section"]) for m in ms if m.get("section")),
            frozenset(str(m["product"]) for m in ms if m.get("product")),
        )


@dataclass
class Parsed:
    filters: (
        dict[str, str] | None
    )  # None: the reply could not be used; {}: valid, no filter
    error: str | None = None


def parse_filters(raw: str, vocab: Vocabulary | None = None) -> Parsed:
    """Validate a model reply. Code fences and prose around the object are tolerated; content is not."""
    text = re.sub(r"```(?:json)?", "", raw or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return Parsed(None, "no_json_object")
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return Parsed(None, "invalid_json")
    try:
        sq = SelfQuery.model_validate(data)
    except ValidationError:
        return Parsed(None, "schema")
    out = {k: v for k, v in sq.model_dump().items() if v}
    if vocab is not None:
        if "section" in out and out["section"] not in vocab.sections:
            return Parsed(None, "unknown_section")
        if "product" in out and out["product"] not in vocab.products:
            return Parsed(None, "unknown_product")
    return Parsed(out)


def chat_extractor(
    chat: ChatModel, sections: Sequence[str], products: Sequence[str]
) -> FilterExtractor:
    system = SELFQUERY_SYSTEM.format(
        sections=", ".join(sorted(sections)), products=", ".join(sorted(products))
    )

    def extract(question: str) -> str:
        return ask(chat, system, question)

    return extract


@dataclass
class Outcome:
    """What a self-query search did: the filters it tried and why it fell back, if it did."""

    filters: (
        dict[str, str] | None
    )  # parsed filters, or None when the reply was unusable
    applied: (
        dict[str, str] | None
    )  # the filters the returned result used (None: unfiltered)
    fallback: str | None  # None, "parse:<why>", "extractor_error", "empty_result"
    raw: str = ""


def self_query_rank(
    search: Callable[[dict[str, Any] | None], list[T]],
    question: str,
    extract: FilterExtractor,
    vocab: Vocabulary | None = None,
) -> tuple[list[T], Outcome]:
    """Search inside the model's filters; fall back to `search(None)` on a bad reply or an empty result.

    `search(filters)` is the retriever's ranked search for a filter dict (None for no filter).
    """
    try:
        raw = extract(question)
    except ProviderFailure:
        return search(None), Outcome(None, None, "extractor_error")
    parsed = parse_filters(raw, vocab)
    if parsed.filters is None:
        return search(None), Outcome(None, None, f"parse:{parsed.error}", raw)
    if not parsed.filters:
        return search(None), Outcome({}, None, None, raw)
    hits = search(parsed.filters)
    if not hits:
        return search(None), Outcome(parsed.filters, None, "empty_result", raw)
    return hits, Outcome(parsed.filters, parsed.filters, None, raw)
