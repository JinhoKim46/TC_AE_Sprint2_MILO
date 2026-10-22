"""Self-query (SPEC D11, D151): parsing, validation and the required fallback to unfiltered search."""

from __future__ import annotations

from typing import Any

import pytest

from milo.errors import ProviderFailure
from milo.rag.chunk import chunk_document
from milo.rag.embed import FakeEmbedder
from milo.rag.selfquery import Vocabulary, parse_filters, self_query_rank
from milo.rag.translate import clean_rewrite, rewrite_query
from milo.rag.vectors import VectorStore

VOCAB = Vocabulary(frozenset({"solutions", "pricing"}), frozenset({"alpha-desk"}))
DOCS = [
    (
        "solutions/alpha-desk",
        "# Alpha desk\n\nThe alpha desk answers calls for a workshop.",
        {"section": "solutions", "product": "alpha-desk", "updated": "2026-10-01"},
    ),
    (
        "pricing/rate-card",
        "# Rate card\n\nUrgent requests carry a surcharge on the monthly fee.",
        {"section": "pricing", "updated": "2026-10-01"},
    ),
]


@pytest.fixture()
def store() -> VectorStore:
    emb = FakeEmbedder()
    chunks = [c for d, t, m in DOCS for c in chunk_document(d, t, m)]
    s = VectorStore()
    s.add(chunks, emb.embed([c.text for c in chunks]))
    return s


def search_fn(store: VectorStore, question: str) -> Any:
    qv = FakeEmbedder().embed([question])[0]
    calls: list[dict[str, Any] | None] = []

    def search(filters: dict[str, Any] | None) -> list[Any]:
        calls.append(filters)
        return store.rank(qv, filters)

    search.calls = calls  # type: ignore[attr-defined]
    return search


def test_parse_valid_and_fenced() -> None:
    assert parse_filters('{"section": "pricing"}', VOCAB).filters == {
        "section": "pricing"
    }
    fenced = (
        'Sure:\n```json\n{"product": "alpha-desk", "updated_after": "2026-01-31"}\n```'
    )
    assert parse_filters(fenced, VOCAB).filters == {
        "product": "alpha-desk",
        "updated_after": "2026-01-31",
    }
    assert parse_filters("{}", VOCAB).filters == {}


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        ("no filters here", "no_json_object"),
        ('{"section": "pricing"', "no_json_object"),
        ("{'section': 'pricing'}", "invalid_json"),
        ('{"colour": "red"}', "schema"),
        ('{"updated_after": "last week"}', "schema"),
        ('{"section": 3}', "schema"),
        ('{"section": "blog"}', "unknown_section"),
        ('{"product": "gamma"}', "unknown_product"),
    ],
)
def test_parse_rejects(raw: str, why: str) -> None:
    got = parse_filters(raw, VOCAB)
    assert got.filters is None and got.error == why


def test_filter_applies_when_it_finds_chunks(store: VectorStore) -> None:
    search = search_fn(store, "alpha desk calls")
    hits, out = self_query_rank(
        search, "alpha desk calls", lambda q: '{"section": "solutions"}', VOCAB
    )
    assert {h.doc_id for h in hits} == {"solutions/alpha-desk"}
    assert out.fallback is None and out.applied == {"section": "solutions"}
    assert search.calls == [{"section": "solutions"}]


def test_fallback_on_parse_failure(store: VectorStore) -> None:
    search = search_fn(store, "urgent surcharge")
    hits, out = self_query_rank(search, "urgent surcharge", lambda q: "I cannot", VOCAB)
    assert {h.doc_id for h in hits} == {
        d for d, _, _ in DOCS
    }  # unfiltered: both documents
    assert out.fallback == "parse:no_json_object" and out.applied is None
    assert search.calls == [None]


def test_fallback_on_empty_result(store: VectorStore) -> None:
    # valid filter that matches no chunk (no pricing document has a product)
    search = search_fn(store, "urgent surcharge")
    hits, out = self_query_rank(
        search,
        "urgent surcharge",
        lambda q: '{"section": "pricing", "product": "alpha-desk"}',
        VOCAB,
    )
    assert len(hits) == store.count()
    assert out.fallback == "empty_result" and out.applied is None
    assert out.filters == {"section": "pricing", "product": "alpha-desk"}
    assert search.calls == [{"section": "pricing", "product": "alpha-desk"}, None]


def test_fallback_on_updated_after_that_matches_nothing(store: VectorStore) -> None:
    search = search_fn(store, "alpha desk")
    hits, out = self_query_rank(
        search, "alpha desk", lambda q: '{"updated_after": "2030-01-01"}', VOCAB
    )
    assert hits and out.fallback == "empty_result"


def test_fallback_on_extractor_error(store: VectorStore) -> None:
    def boom(q: str) -> str:
        raise ProviderFailure("provider_error:timeout")

    search = search_fn(store, "alpha desk")
    hits, out = self_query_rank(search, "alpha desk", boom, VOCAB)
    assert hits and out.fallback == "extractor_error"


def test_no_filter_is_not_a_fallback(store: VectorStore) -> None:
    hits, out = self_query_rank(
        search_fn(store, "alpha"), "alpha", lambda q: "{}", VOCAB
    )
    assert hits and out.fallback is None and out.applied is None


def test_rewrite_keeps_question_on_empty_or_failure() -> None:
    assert clean_rewrite('  "How long is set-up?"\nextra', "q") == "How long is set-up?"
    assert clean_rewrite("Query: set-up time", "q") == "set-up time"
    assert clean_rewrite("   ", "original") == "original"

    def boom(q: str) -> str:
        raise ProviderFailure("provider_error:unavailable")

    assert rewrite_query("original", boom) == ("original", "")
    assert rewrite_query("original", lambda q: "better query") == (
        "better query",
        "better query",
    )
