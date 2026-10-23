"""Runner changes of the data-v2 protocol: split loading, cross-owner `not_found` cases, judge-pool separation.

All offline; every data file is a small temporary copy, so the tests do not depend on the generated data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evals import common
from evals.agent import run_tools
from evals.discovery import run_discovery as rd
from evals.rag import run_rag_eval as rag

REAL_CONV = rd.CONV_DIR


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


# ---------------------------------------------------------------- discovery: splits


def _conv_dir(tmp_path: Path, manifest: dict[str, Any] | None) -> Path:
    d = tmp_path / "conversations"
    d.mkdir()
    if manifest is not None:
        (d / "splits.json").write_text(json.dumps(manifest), encoding="utf-8")
    for cid, split in (("C-1", "dev"), ("C-2", "test"), ("A-2", "test")):
        _jsonl(d / f"{cid}.jsonl", [{"turn": 1, "owner_text": "hi"}])
        (d / f"{cid}.audited.expected.json").write_text(
            json.dumps({"user_id": "owner-c", "split": split, "turns": []}),
            encoding="utf-8",
        )
    return d


@pytest.fixture
def conv_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("MILO_FINAL", raising=False)
    d = _conv_dir(tmp_path, {"dev": ["C-1"], "test": ["C-2"]})
    monkeypatch.setattr(rd, "CONV_DIR", d)
    monkeypatch.setattr(rd, "SPLITS_PATH", d / "splits.json")
    return d


def test_without_a_manifest_the_legacy_dev_set_loads_and_no_test_set_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rd, "SPLITS_PATH", tmp_path / "missing.json")
    assert rd.dev_convs() == ("A-1", "B-1", "E-1")
    assert rd.test_convs() == ()
    assert rd.CONV_DIR == REAL_CONV


def test_the_manifest_decides_the_dev_and_test_ids(conv_dir: Path) -> None:
    assert rd.dev_convs() == ("C-1",)
    assert rd.test_convs() == ("C-2",)
    msgs, expected = rd.load_conversation("C-1")
    assert msgs[0]["owner_text"] == "hi" and expected["split"] == "dev"


def test_a_test_conversation_needs_milo_final(
    conv_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(PermissionError, match="test conversation"):
        rd.load_conversation("C-2")
    monkeypatch.setenv("MILO_FINAL", "1")
    assert rd.load_conversation("C-2")[1]["split"] == "test"


def test_retired_conversations_are_never_loaded_even_under_milo_final(
    conv_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MILO_FINAL", "1")
    for conv in ("A-2", "B-2", "E-2"):
        with pytest.raises(PermissionError, match="retired"):
            rd.load_conversation(conv)


def test_a_conversation_outside_every_split_is_refused(conv_dir: Path) -> None:
    with pytest.raises(ValueError, match="no split"):
        rd.load_conversation("Z-9")


def test_a_retired_id_in_a_split_or_a_duplicate_id_is_a_manifest_error(
    tmp_path: Path,
) -> None:
    for manifest in (
        {"dev": ["A-1", "A-2"], "test": []},
        {"dev": ["C-1"], "test": ["C-1"]},
        {"dev": ["C-1"], "test": [], "retired": ["C-1"]},
    ):
        path = tmp_path / "splits.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(ValueError):
            rd.load_splits(path)


def test_the_split_field_of_a_file_must_agree_with_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MILO_FINAL", raising=False)
    d = _conv_dir(tmp_path, {"dev": ["C-2"], "test": []})  # C-2's file says test
    monkeypatch.setattr(rd, "CONV_DIR", d)
    monkeypatch.setattr(rd, "SPLITS_PATH", d / "splits.json")
    with pytest.raises(ValueError, match="split"):
        rd.load_conversation("C-2")


def test_audited_rows_use_only_manifest_dev_ids(conv_dir: Path) -> None:
    row = {"conversation": "C-2", "turn": 1, "expected": {"facts": []}}
    with pytest.raises(ValueError, match="only dev rows"):
        rd.audited_rows([row])


def test_the_final_run_needs_milo_final_and_a_test_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import argparse

    monkeypatch.setenv("MILO_LIVE", "1")
    monkeypatch.delenv("MILO_FINAL", raising=False)
    args = argparse.Namespace(
        final=True,
        model="m",
        price_in=1.0,
        price_out=1.0,
        max_cost=0.1,
        reason="r",
        out=None,
    )
    with pytest.raises(SystemExit, match="MILO_FINAL=1"):
        rd.run_live(args)
    monkeypatch.setenv("MILO_FINAL", "1")
    monkeypatch.setattr(rd, "SPLITS_PATH", tmp_path / "missing.json")  # no test ids
    monkeypatch.setattr(rd, "TEST_PATH", tmp_path / "none.json")
    with pytest.raises(SystemExit, match="no test conversations"):
        rd.run_live(args)


def test_the_committed_dry_result_still_replays() -> None:
    assert rd.replay(rd.DRY_PATH) == 0


# ---------------------------------------------------------------- tools: splits and cross-owner rows


def _case(cid: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": cid,
        "user_id": "owner-a",
        "query": f"query {cid}",
        "expected_calls": [],
        "match": "exact",
        **extra,
    }


def _withdraw(cid: str, owner: str, sub: int, **extra: Any) -> dict[str, Any]:
    return _case(
        cid,
        user_id=owner,
        expected_calls=[
            {"name": "withdraw_submission", "args": {"submission_id": sub}}
        ],
        **extra,
    )


def test_load_cases_defaults_to_dev_and_a_row_without_split_is_dev(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MILO_FINAL", raising=False)
    path = _jsonl(
        tmp_path / "c.jsonl",
        [_case("a"), _case("b", split="dev"), _case("c", split="test")],
    )
    assert [c["id"] for c in run_tools.load_cases(path)] == ["a", "b"]


def test_test_cases_need_milo_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _jsonl(tmp_path / "c.jsonl", [_case("a"), _case("c", split="test")])
    monkeypatch.delenv("MILO_FINAL", raising=False)
    with pytest.raises(PermissionError):
        run_tools.load_cases(path, "test")
    monkeypatch.setenv("MILO_FINAL", "1")
    assert [c["id"] for c in run_tools.load_cases(path)] == ["c"]
    assert [c["id"] for c in run_tools.load_cases(path, "dev")] == ["a"]


def test_an_unknown_split_or_outcome_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MILO_FINAL", raising=False)
    with pytest.raises(ValueError, match="split"):
        run_tools.load_cases(_jsonl(tmp_path / "a.jsonl", [_case("a", split="val")]))
    with pytest.raises(ValueError, match="expected_outcome"):
        run_tools.load_cases(
            _jsonl(tmp_path / "b.jsonl", [_case("a", expected_outcome="boom")])
        )


def _point_at(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows: list[dict]
) -> None:
    cases = _jsonl(tmp_path / "tool_cases.jsonl", rows)
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text(
        json.dumps({"data/tool_cases.jsonl": common.file_sha256(cases)})
    )
    monkeypatch.setattr(run_tools, "CASES_PATH", cases)
    monkeypatch.setattr(run_tools, "MANIFEST_PATH", manifest)
    monkeypatch.delenv("MILO_FINAL", raising=False)


def test_a_cross_owner_row_with_outcome_not_found_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # submission 2 belongs to owner-b
    _point_at(
        monkeypatch,
        tmp_path,
        [_withdraw("x1", "owner-a", 2, expected_outcome="not_found", split="dev")],
    )
    assert run_tools.check_inputs()


def test_a_cross_owner_row_without_the_outcome_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _point_at(monkeypatch, tmp_path, [_withdraw("x1", "owner-a", 2)])
    with pytest.raises(SystemExit, match="withdraws a submission of owner-b"):
        run_tools.check_inputs()


def test_not_found_on_the_users_own_submission_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _point_at(
        monkeypatch,
        tmp_path,
        [_withdraw("x1", "owner-a", 1, expected_outcome="not_found")],
    )
    with pytest.raises(SystemExit, match="expects not_found"):
        run_tools.check_inputs()


def test_a_schema_invalid_expectation_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = _case(
        "x1",
        expected_calls=[{"name": "estimate_cost", "args": {"daily_volume": 5}}],
        expected_outcome="ok",
    )
    _point_at(monkeypatch, tmp_path, [bad])
    with pytest.raises(SystemExit, match="schema-invalid"):
        run_tools.check_inputs()


def test_dry_run_keeps_the_expected_outcome_on_the_row_and_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        _withdraw("x1", "owner-a", 2, expected_outcome="not_found", split="dev"),
        _case("x2", split="dev"),
        _case("x3", split="test"),
    ]
    _point_at(monkeypatch, tmp_path, rows)
    doc = run_tools.run_dry(tmp_path / "tools_dry.json")
    assert [r["id"] for r in doc["rows"]] == ["x1", "x2"]  # the test row is not run
    assert doc["rows"][0]["expected_outcome"] == "not_found"
    assert "expected_outcome" not in doc["rows"][1]
    assert doc["metrics"]["failed_cases"] == []
    assert run_tools.replay([tmp_path / "tools_dry.json"]) == 0


# ---------------------------------------------------------------- rag: judge-pool separation


def _pool_item(pid: str, qid: str = "q1") -> dict[str, Any]:
    return {
        "pid": pid,
        "qid": qid,
        "question": "What?",
        "contexts": [{"chunk_id": "d#0", "doc_id": "d", "text": "t"}],
        "answer": "An answer.",
        "source": "pool",
    }


def _label(pid: str, faithfulness: int = 1) -> dict[str, Any]:
    return {
        "qid": pid,
        "faithfulness": faithfulness,
        "relevance": 2,
        "context_relevance": [1],
        "labeller": "auditor",
    }


def test_pool_rows_are_recognised_by_source_or_pid() -> None:
    assert rag.is_pool_row({"qid": "q", "source": "pool"})
    assert rag.is_pool_row({"qid": "q", "pid": "p1"})
    assert not rag.is_pool_row({"qid": "q", "answer": "a"})


def test_assemble_refuses_pool_rows_among_product_rows() -> None:
    gen_rows = [{"qid": "q1"}, _pool_item("p1")]
    with pytest.raises(rag.PoolRowError, match="judge-pool rows"):
        rag.assemble(gen_rows, [])
    with pytest.raises(rag.PoolRowError):
        rag.assemble([], [{**_label("p1"), "source": "pool"}])


def test_a_product_result_with_a_pool_row_is_refused(tmp_path: Path) -> None:
    doc = json.loads(rag.DRY_RESULT.read_text(encoding="utf-8"))
    doc["rows"].append(_pool_item("p1"))
    bad = tmp_path / "rag_dry.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(rag.PoolRowError):
        rag.replay_judge_file(bad)
    with pytest.raises(rag.PoolRowError):
        rag.replay_generation_file(bad)
    with pytest.raises(rag.PoolRowError):
        rag.result_doc(doc["rows"], model="m", command="c", inputs={}, extra={})


def test_the_committed_product_replay_is_unchanged() -> None:
    assert rag.replay_judge_file(rag.DRY_RESULT)


def test_replay_and_template_refuse_a_file_under_the_pool_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argparse

    inside = rag.POOL_DIR / "answers.jsonl"
    with pytest.raises(rag.PoolRowError):
        rag.replay(argparse.Namespace(result=str(inside)))
    with pytest.raises(rag.PoolRowError):
        rag.cmd_template(argparse.Namespace(generation=str(inside)))


def test_load_pool_joins_answers_with_labels_by_pid(tmp_path: Path) -> None:
    items = _jsonl(tmp_path / "answers.jsonl", [_pool_item("p1"), _pool_item("p2")])
    labels = _jsonl(tmp_path / "labels.jsonl", [_label("p1", 0), _label("p2")])
    rows, problems = rag.load_pool(items, labels)
    assert problems == [] and [r["pid"] for r in rows] == ["p1", "p2"]
    assert rows[0]["labels"]["faithfulness"] == 0 and rows[0]["source"] == "pool"


def test_load_pool_reports_bad_items_and_labels(tmp_path: Path) -> None:
    dup = _pool_item("p1")
    nosrc = {**_pool_item("p3"), "source": "real"}
    items = _jsonl(tmp_path / "answers.jsonl", [dup, dup, nosrc])
    labels = _jsonl(
        tmp_path / "labels.jsonl", [_label("p1", 2)]
    )  # invalid value, p3 unlabelled
    _, problems = rag.load_pool(items, labels)
    text = " | ".join(problems)
    assert "duplicate pid p1" in text and "source must be 'pool'" in text
    assert "faithfulness" in text and "missing label for sampled qid p3" in text


# ---------------------------------------------------------------- discovery: company and uploads per conversation


def _clone_b1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, header: bool) -> Path:
    """B-1 copied as N-1 into a temporary conversations folder, its upload moved from the turn to the header."""
    monkeypatch.delenv("MILO_FINAL", raising=False)
    d = tmp_path / "conversations"
    d.mkdir()
    (d / "splits.json").write_text(json.dumps({"dev": ["N-1"], "test": []}))
    (d / "N-1.jsonl").write_text((REAL_CONV / "B-1.jsonl").read_text(encoding="utf-8"))
    exp = json.loads((REAL_CONV / "B-1.audited.expected.json").read_text("utf-8"))
    if header:
        for t in exp["turns"]:
            t.pop("upload", None)
        exp["company"] = "b.yaml"
        exp["uploads"] = {"22": "ironbridge-procedure.txt"}
    (d / "N-1.audited.expected.json").write_text(json.dumps(exp), encoding="utf-8")
    script = json.loads(rd.DRY_SCRIPT.read_text(encoding="utf-8"))
    scripts = tmp_path / "dry_scripts"
    scripts.mkdir()
    (scripts / "n.json").write_text(
        json.dumps({"conversations": {"N-1": script["conversations"]["B-1"]}})
    )
    monkeypatch.setattr(rd, "CONV_DIR", d)
    monkeypatch.setattr(rd, "SPLITS_PATH", d / "splits.json")
    monkeypatch.setattr(rd, "DRY_SCRIPT_DIR", scripts)
    return d


def test_header_upload_and_company_give_the_same_rows_as_the_legacy_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clone_b1(tmp_path, monkeypatch, header=True)
    script = rd.load_dry_script()
    assert "N-1" in script["conversations"] and "B-1" in script["conversations"]
    new = rd.run_conversation("N-1", rd.DryChat(script))
    monkeypatch.undo()
    old = rd.run_conversation("B-1", rd.DryChat(rd.load_dry_script()))
    assert [r["upload"] for r in new if r["upload"]] == [
        "data/demo/uploads/ironbridge-procedure.txt"
    ]

    def strip(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            r = {k: v for k, v in r.items() if k != "conversation"}
            exp = {k: v for k, v in r["expected"].items() if k != "upload"}
            exp["flags"] = {k: v for k, v in exp["flags"].items() if k != "upload"}
            r["expected"] = exp
            out.append(r)
        return out

    a, b = strip(new), strip(old)
    assert a == b


def test_a_header_naming_a_missing_file_or_a_path_outside_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(FileNotFoundError, match="nope.yaml"):
        rd.conversation_company({"company": "nope.yaml"})
    with pytest.raises(FileNotFoundError):
        rd.conversation_uploads({"uploads": {"3": "nope.txt"}})
    # only the file name counts: a traversal resolves inside the folder or fails
    assert (
        rd.conversation_company({"company": "../../../etc/b.yaml"}).parent
        == rd.COMPANIES
    )


def test_a_conversation_without_a_header_keeps_the_legacy_behaviour() -> None:
    exp = json.loads((REAL_CONV / "B-1.audited.expected.json").read_text("utf-8"))
    assert list(rd.conversation_uploads(exp)) == [22]
    assert rd.conversation_company({}) is None
    assert rd.conversation_uploads({}) == {}


def test_dry_run_skips_a_conversation_without_an_entry_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    d = _clone_b1(tmp_path, monkeypatch, header=True)
    (d / "splits.json").write_text(json.dumps({"dev": ["N-1", "M-1"], "test": []}))
    monkeypatch.setattr(
        rd, "DRY_SCRIPT_DIR", tmp_path / "none"
    )  # no entry for N-1 or M-1
    doc = rd.run_dry(tmp_path / "out.json")
    err = capsys.readouterr().err
    assert "no dry script entry for N-1" in err and "no dry script entry for M-1" in err
    assert doc["conversations"] == []


def test_a_dry_script_defined_twice_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clone_b1(tmp_path, monkeypatch, header=True)
    (tmp_path / "dry_scripts" / "dup.json").write_text(
        json.dumps({"conversations": {"N-1": {}}})
    )
    with pytest.raises(ValueError, match="twice"):
        rd.load_dry_script()
