"""M1 chunker tests (SPEC section 4, D9). Offline; no model."""

from __future__ import annotations

import pytest

from evals import common
from milo.rag.chunk import (
    DEFAULT_OVERLAP,
    DEFAULT_SIZE,
    SETTINGS,
    chunk_document,
    chunks_sha256,
    normalise_metadata,
    parse_front_matter,
    split_text,
)
from milo.rag.ingest import load_kb

DOC = "# Title\n\nIntro paragraph.\n\n## One\n\nFirst section text.\n\n## Two\n\nSecond section text."


def test_defaults_and_settings() -> None:
    assert (DEFAULT_SIZE, DEFAULT_OVERLAP) == (800, 100)
    assert SETTINGS == ((800, 100), (400, 50), (1500, 150))


def test_heading_starts_a_chunk() -> None:
    parts = split_text(DOC, 800, 100)
    assert len(parts) == 3
    assert parts[1].startswith("## One") and parts[2].startswith("## Two")
    assert "Second" not in parts[1]


@pytest.mark.parametrize(("size", "overlap"), SETTINGS)
def test_chunks_respect_size(size: int, overlap: int) -> None:
    text = "# H\n\n" + "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(30))
    long_para = "x" * (size * 3 + 17)
    for piece in split_text(text + "\n\n" + long_para, size, overlap):
        assert 0 < len(piece) <= size


def test_overlap_repeats_the_tail_inside_a_section() -> None:
    paras = [f"para{i} " + "lorem " * 20 for i in range(6)]
    parts = split_text("# H\n\n" + "\n\n".join(paras), 300, 60)
    assert len(parts) > 1
    tail = parts[0][-30:].split(" ", 1)[-1]
    assert tail in parts[1]


def test_overlap_does_not_cross_a_heading() -> None:
    parts = split_text("# A\n\n" + "alpha " * 10 + "\n\n# B\n\nbeta", 800, 100)
    assert len(parts) == 2 and "alpha" not in parts[1]


def test_paragraph_that_fits_is_never_cut() -> None:
    quote = "The module never takes card payments over the phone."
    filler = "\n\n".join("filler sentence number %d " % i * 6 for i in range(40))
    parts = split_text(f"# H\n\n{filler}\n\n{quote}\n\n{filler}", 400, 50)
    assert any(quote in p for p in parts)


def test_long_paragraph_is_windowed_with_overlap() -> None:
    para = "".join(chr(97 + i % 26) for i in range(2000))
    parts = split_text(para, 800, 100)
    assert [len(p) for p in parts] == [800, 800, 600]
    assert parts[0][-100:] == parts[1][:100]


def test_invalid_settings_and_empty_text() -> None:
    with pytest.raises(ValueError):
        split_text("x", 100, 100)
    assert split_text("", 800, 100) == []
    assert split_text("\n\n  \n\n", 800, 100) == []


def test_chunk_ids_metadata_and_hash() -> None:
    chunks = chunk_document("kb/x", DOC, {"section": "solutions", "product": None})
    assert [c.chunk_id for c in chunks] == ["kb/x#0", "kb/x#1", "kb/x#2"]
    assert chunks[0].metadata == {"section": "solutions"}  # an absent key stays absent
    assert chunks_sha256(chunks) == chunks_sha256(chunk_document("kb/x", DOC, {}))


def test_title_prefix_after_splitting() -> None:
    chunks = chunk_document("kb/x", DOC, {"title": "A title"})
    # chunk 0 already opens with the article heading
    assert chunks[0].text.startswith("# Title")
    assert chunks[1].text == "A title\n\n## One\n\nFirst section text."
    assert [len(c.text) for c in chunk_document("kb/x", DOC, {})] == [
        len(p) for p in split_text(DOC)
    ]


def test_updated_num_only_when_updated_exists() -> None:
    assert normalise_metadata({"updated": "2026-10-01"})["updated_num"] == 20261001
    assert "updated_num" not in normalise_metadata({"section": "a"})
    assert "updated_num" not in normalise_metadata({"updated": "soon"})


def test_front_matter() -> None:
    meta, body = parse_front_matter(
        "---\ntitle: Case: a thing\nsection: cases\n---\n# H\n\nBody"
    )
    assert meta == {"title": "Case: a thing", "section": "cases"}
    assert body.startswith("# H")
    assert parse_front_matter("# H") == ({}, "# H")


def test_every_dev_quote_lands_in_a_chunk_at_the_product_setting() -> None:
    """SPEC D78 quote rule: at 800/100 no answerable dev question is left without a relevant chunk."""
    docs = {d.doc_id: d for d in load_kb(common.DATA / "kb")}
    assert len(docs) == 24
    chunks = {
        i: chunk_document(d.doc_id, d.text, d.metadata, 800, 100)
        for i, d in docs.items()
    }
    rows = [r for r in common.load_gold("dev") if r["gold_evidence"]]
    assert rows
    for row in rows:
        for ev in row["gold_evidence"]:
            quote = " ".join(ev["quote"].split()).lower()
            assert any(
                quote in " ".join(c.text.split()).lower() for c in chunks[ev["doc_id"]]
            ), row["qid"]
