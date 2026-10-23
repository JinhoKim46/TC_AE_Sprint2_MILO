"""M1 vector store, retriever, embedders and the retrieval eval harness. Offline; fake embedder only."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from milo.errors import ProviderFailure
from milo.rag.chunk import chunk_document
from milo.rag.embed import FakeEmbedder, OpenRouterEmbedder, live_embedder
from milo.rag.retrieve import Retriever
from milo.rag.vectors import VectorStore, build_where
from milo.types import Document

ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    Document(
        "solutions/phone",
        "# Phone\n\nThe phone module answers calls for a salon.",
        {"section": "solutions", "product": "phone", "updated": "2026-10-01"},
    ),
    Document(
        "pricing/card",
        "# Prices\n\nUrgent requests carry a surcharge of twenty percent.",
        {"section": "pricing", "updated": "2026-11-15"},
    ),
    Document("misc/note", "# Note\n\nA plain note about tractors and weather.", {}),
]


def loaded(min_score: float = 0.0) -> Retriever:
    r = Retriever(FakeEmbedder(), min_score=min_score)
    r.ingest(DOCS)
    return r


def test_fake_embedder_is_deterministic_and_normalised() -> None:
    e = FakeEmbedder()
    a, b = e.embed(["calls for a salon"]), e.embed(["calls for a salon"])
    assert a == b
    assert abs(sum(x * x for x in a[0]) - 1.0) < 1e-9


def test_search_returns_sources_with_chunk_ids_and_scores() -> None:
    hits = loaded().search("which module answers calls for a salon")
    assert (
        hits[0].chunk_id == "solutions/phone#0" and hits[0].doc_id == "solutions/phone"
    )
    assert 0.0 < hits[0].score <= 1.0 and "salon" in hits[0].text
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_abstains_below_min_score_and_on_empty_index() -> None:
    assert loaded(min_score=0.99).search("which module answers calls") == []
    calls: list[Any] = []

    class Spy:
        def embed(self, texts: list[str]) -> list[list[float]]:
            calls.append(texts)
            return [[1.0] for _ in texts]

    assert Retriever(Spy()).search("anything") == [] and calls == []


def test_metadata_filters() -> None:
    r = loaded()
    assert {h.doc_id for h in r.search("module", filters={"section": "pricing"})} == {
        "pricing/card"
    }
    assert {h.doc_id for h in r.search("module", filters={"product": "phone"})} == {
        "solutions/phone"
    }
    after = r.search("module", filters={"updated_after": "2026-10-15"})
    assert {h.doc_id for h in after} == {
        "pricing/card"
    }  # the doc without `updated` never matches
    both = r.search("module", filters={"section": "pricing", "product": "phone"})
    assert both == []


def test_build_where_shapes() -> None:
    assert build_where(None) is None and build_where({"section": None}) is None
    assert build_where({"section": "a"}) == {"section": "a"}
    assert build_where({"updated_after": 20261001}) == {
        "updated_num": {"$gt": 20261001}
    }
    assert build_where({"section": "a", "product": "b"}) == {
        "$and": [{"section": "a"}, {"product": "b"}]
    }


def test_reingest_replaces_a_document_and_keeps_others() -> None:
    r = loaded()
    n = r.count()
    r.ingest([Document("misc/note", "# Note\n\nOnly one short line.", {})])
    assert r.count() == n  # one chunk replaced by one chunk
    assert any("one short line" in h.text for h in r.search("short line"))
    assert not any("tractors" in h.text for h in r.search("tractors weather"))


def test_persistence_on_disk(tmp_path: Path) -> None:
    emb = FakeEmbedder()
    chunks = [c for d in DOCS for c in chunk_document(d.doc_id, d.text, d.metadata)]
    store = VectorStore(tmp_path / "idx")
    store.add(chunks, emb.embed([c.text for c in chunks]))
    store.write_manifest({"model": "fake", "chunk_count": len(chunks)})
    q = emb.embed(["urgent surcharge"])[0]
    before = store.search(q, 3)
    again = VectorStore(tmp_path / "idx")
    assert again.count() == len(chunks)
    assert again.read_manifest() == {"model": "fake", "chunk_count": len(chunks)}
    assert [(h.chunk_id, h.score) for h in again.search(q, 3)] == [
        (h.chunk_id, h.score) for h in before
    ]


def test_ephemeral_stores_are_isolated() -> None:
    a, b = loaded(), Retriever(FakeEmbedder())
    assert a.count() > 0 and b.count() == 0


def test_openrouter_embedder_batches_and_types_failures() -> None:
    seen: list[int] = []

    class Client:
        def __init__(self) -> None:
            self.embeddings = self

        def create(self, model: str, input: list[str]) -> Any:
            seen.append(len(input))
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[1.0, 0.0]) for _ in input]
            )

    emb = OpenRouterEmbedder("k", client=Client(), batch=64)
    assert len(emb.embed([str(i) for i in range(150)])) == 150
    assert seen == [64, 64, 22] and emb.calls == 3

    class Broken:
        embeddings = SimpleNamespace(
            create=lambda **_: (_ for _ in ()).throw(TimeoutError())
        )

    with pytest.raises(ProviderFailure):
        OpenRouterEmbedder("k", client=Broken()).embed(["x"])


def test_live_embedder_refuses_without_milo_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MILO_LIVE", raising=False)
    with pytest.raises(PermissionError):
        live_embedder()


def _runner() -> Any:
    spec = importlib.util.spec_from_file_location(
        "run_retrieval", ROOT / "evals/retrieval/run_retrieval.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_retrieval"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_metrics_from_ranks() -> None:
    m = _runner().metrics_from_ranks
    assert m([1], 1)["ndcg5"] == 1.0 and m([1], 1)["mrr"] == 1.0
    assert m([], 1) == {
        "recall5": 0.0,
        "ndcg5": 0.0,
        "mrr": 0.0,
        "p5": 0.0,
        "ap": 0.0,
        "first_relevant_rank": None,
    }
    two = m([2, 7], 2)
    assert two["recall5"] == 1.0 and two["mrr"] == 0.5 and two["p5"] == 0.2
    assert abs(two["ap"] - (1 / 2 + 2 / 7) / 2) < 1e-12


def test_accepted_alternative_scores_like_the_gold_chunk() -> None:
    """D168: a twin passage at rank 1 is a full hit; gold plus twin in the top 5 does not score above 1."""
    run = _runner()
    row = {"relevant": ["a#1"]}
    assert run.accepted_metrics(row, [1])["ndcg5"] == 1.0
    assert run.accepted_metrics(row, [1, 2])["ndcg5"] == 1.0
    assert run.accepted_metrics(row, [3])["mrr"] == 1 / 3
    store = VectorStore(None)
    chunks = [c for d in DOCS for c in chunk_document(d.doc_id, d.text, d.metadata)]
    emb = FakeEmbedder()
    store.add(chunks, emb.embed([c.text for c in chunks]))
    q = {"qid": "x", "type": "paraphrase", "question": "calls", "filters": None,
         "quotes": [{"doc_id": "misc/note", "quote": "tractors"}],
         "alt_quotes": [{"doc_id": "solutions/phone", "quote": "answers calls"}]}  # fmt: skip
    rows = run.rank_rows(store, chunks, [q], {"x": emb.embed(["calls for a salon"])[0]})
    assert rows[0]["accepted"] == ["solutions/phone#0"]
    derived = run.derive(rows)
    assert derived["accepted_rows"] == ["x"]
    assert (
        derived["summary_accepted"]["ndcg5"]["mean"]
        >= derived["summary"]["ndcg5"]["mean"]
    )


def test_dry_result_replays_and_a_tampered_row_does_not(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _runner()
    stored = ROOT / "evals/results/retrieval/retrieval_dry.json"
    assert run.replay(SimpleNamespace(result=str(stored))) == 0
    data = json.loads(stored.read_text(encoding="utf-8"))
    data["rows"][0]["relevant_ranks"] = [4]
    bad = tmp_path / "retrieval_dry.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    assert (
        run.replay_one(bad) is False
    )  # no embeddings file beside the copy: the recompute check alone
    assert "MISMATCH" in capsys.readouterr().out


def test_live_refuses_without_milo_live(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MILO_LIVE", raising=False)
    assert _runner().main([]) == 2
    assert "MILO_LIVE=1" in capsys.readouterr().err
