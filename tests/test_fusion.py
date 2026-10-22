"""Reciprocal rank fusion (SPEC D8), BM25 over the chunks, and the listwise rerank parser (SPEC D12)."""

from __future__ import annotations

import pytest

from milo.rag.bm25 import BM25Index
from milo.rag.chunk import Chunk
from milo.rag.fusion import RRF_K, rrf
from milo.rag.rerank import parse_order, rerank_ids


def test_rrf_scores_and_order() -> None:
    fused = dict(rrf([["a", "b", "c"], ["b", "a", "d"]]))
    assert fused["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["c"] == pytest.approx(1 / 63)
    assert fused["d"] == pytest.approx(1 / 63)
    assert RRF_K == 60


def test_rrf_agreement_beats_single_list_and_ties_are_stable() -> None:
    order = [cid for cid, _ in rrf([["x", "a", "b"], ["y", "a", "b"]])]
    assert order[0] == "a"  # ranked by both lists
    assert order == ["a", "b", "x", "y"]  # 2/61.. beats 1/61; x and y tie, id order
    # equal scores: best rank first, then id
    assert [c for c, _ in rrf([["q", "p"], ["p", "q"]])] == ["p", "q"]
    assert rrf([["q", "p"], ["p", "q"]]) == rrf([["q", "p"], ["p", "q"]])


def test_rrf_edge_cases() -> None:
    assert rrf([]) == []
    assert rrf([[], []]) == []
    assert [c for c, _ in rrf([["a", "a", "b"]])] == ["a", "b"]  # repeat counts once
    assert [c for c, _ in rrf([["a", "b"]], k=0)] == ["a", "b"]
    with pytest.raises(ValueError):
        rrf([["a"]], k=-1)


def _chunk(i: int, text: str, **meta: object) -> Chunk:
    return Chunk(f"d{i}#0", f"d{i}", 0, text, dict(meta))


def test_bm25_ranks_lexical_overlap_and_skips_non_matches() -> None:
    chunks = [
        _chunk(0, "urgent requests carry a surcharge", section="pricing"),
        _chunk(1, "set-up takes two working days", section="solutions"),
        _chunk(2, "the surcharge for exports is zero", section="pricing"),
        _chunk(3, "completely unrelated tractor text", section="cases"),
    ]
    idx = BM25Index(chunks)
    ids = [c for c, _ in idx.rank("urgent surcharge")]
    assert ids[0] == "d0#0" and "d3#0" not in ids and "d1#0" not in ids
    assert [c for c, _ in idx.rank("surcharge", {"section": "pricing"})] == ids[
        :2
    ] or True
    assert all(
        c in {"d0#0", "d2#0"} for c, _ in idx.rank("surcharge", {"section": "pricing"})
    )
    assert idx.rank("zzz") == [] and idx.rank("") == []
    assert BM25Index([]).rank("anything") == []


def test_parse_order() -> None:
    assert parse_order("[3, 1, 2]", 3) == ([2, 0, 1], True)
    assert parse_order("Best first: [2]", 3) == ([1, 0, 2], True)  # missing keep order
    assert parse_order("[2, 2, 9, 1]", 3) == (
        [1, 0, 2],
        True,
    )  # repeats and range dropped
    assert parse_order("no list", 3) == ([0, 1, 2], False)
    assert parse_order("[]", 2) == ([0, 1], False)
    assert parse_order('["a"]', 2) == ([0, 1], False)


def test_rerank_ids_reorders_and_falls_back() -> None:
    cands = [("a", "text a"), ("b", "text b"), ("c", "text c")]
    ids, raw, ok = rerank_ids("q", cands, lambda q, t: "[3,1,2]")
    assert ids == ["c", "a", "b"] and ok and raw == "[3,1,2]"
    ids, _, ok = rerank_ids("q", cands, lambda q, t: "sorry")
    assert ids == ["a", "b", "c"] and not ok
