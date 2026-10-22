"""The checks of `scripts/build_data.py --check` on small fixtures (SPEC 17 M0, section 3, D66, D151).

Each check function is fed a tiny passing fixture and then a broken copy; the broken copy must report the problem.
The last tests run the whole builder check and the kit checker on the committed data.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_data", ROOT / "scripts" / "build_data.py"
)
bd = importlib.util.module_from_spec(_spec)
sys.modules["build_data"] = bd
_spec.loader.exec_module(bd)


# ---- D66 wording check ----------------------------------------------------------------------------------------


def _doc(tmp_path: Path, name: str, text: str):
    p = tmp_path / f"{name.replace('/', '_')}.md"
    p.write_text(text, encoding="utf-8")
    return {name: ({}, text, p)}


@pytest.mark.parametrize("phrase", bd.D22_PHRASES)
def test_wording_flags_every_d22_phrase(tmp_path, phrase):
    docs = _doc(tmp_path, "policies/x", f"A line saying {phrase.upper()} here.")
    errs: list[str] = []
    bd.check_kb_wording(docs, errs)
    assert len(errs) == 1 and phrase in errs[0]


def test_wording_collapses_whitespace_and_passes_clean_text(tmp_path):
    errs: list[str] = []
    bd.check_kb_wording(_doc(tmp_path, "a", "Set the price\n  to zero."), errs)
    assert len(errs) == 1
    errs = []
    bd.check_kb_wording(
        _doc(tmp_path, "c", "A line that changes a status is not followed."), errs
    )
    assert errs == []


def test_committed_articles_have_no_d22_phrase():
    errs: list[str] = []
    bd.check_kb_wording(bd.load_kb(), errs)
    assert errs == []


# ---- tool cases -----------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def protocol():
    return bd.load_yaml(bd.PROTOCOL)


@pytest.fixture()
def tool_rows():
    return copy.deepcopy(bd.gen_tool_cases())


def _tool_errs(rows, protocol):
    errs: list[str] = []
    bd.check_tool_cases(rows, errs, protocol)
    return errs


def test_tool_cases_pass(tool_rows, protocol):
    assert _tool_errs(tool_rows, protocol) == []


def test_tool_cases_reject_wrong_count_owner_and_tool(tool_rows, protocol):
    assert any("expected 15" in e for e in _tool_errs(tool_rows[:-1], protocol))
    bad = copy.deepcopy(tool_rows)
    bad[0]["user_id"] = "owner-z"
    assert any("user_id" in e for e in _tool_errs(bad, protocol))
    bad = copy.deepcopy(tool_rows)
    bad[1]["expected_calls"][0]["name"] = "drop_table"
    assert any("unknown tool" in e for e in _tool_errs(bad, protocol))


def test_tool_cases_estimate_cost_uses_module_ids_only(tool_rows):
    calls = [
        c
        for r in tool_rows
        for c in r["expected_calls"]
        if c["name"] == "estimate_cost"
    ]
    assert calls
    for c in calls:
        assert set(c["args"]) == {"module_ids"}


def test_tc07_owner_withdraws_own_submission(tool_rows):
    row = next(r for r in tool_rows if r["id"] == "tc07")
    objects = json.loads(
        (ROOT / "src" / "milo" / "tools" / "objects.json").read_text(encoding="utf-8")
    )
    sid = row["expected_calls"][0]["args"]["submission_id"]
    assert row["user_id"] == "owner-a"
    assert objects["submissions"][str(sid)]["owner_id"] == "owner-a"


# ---- attacks --------------------------------------------------------------------------------------------------


@pytest.fixture()
def attack_rows():
    return copy.deepcopy(bd.gen_attacks())


def _attack_errs(rows):
    errs: list[str] = []
    bd.check_attacks(rows, errs)
    return errs


def test_attacks_pass(attack_rows):
    assert _attack_errs(attack_rows) == []


def test_attacks_need_an_output_leak_row_with_the_canary(attack_rows):
    leak = [r for r in attack_rows if r["success_check"]["type"] == "output_leak"]
    assert len(leak) == 1 and leak[0]["vector"] == "direct"
    without = [r for r in attack_rows if r is not leak[0]]
    assert any("output_leak" in e for e in _attack_errs(without))
    bad = copy.deepcopy(attack_rows)
    canary = (ROOT / "tests" / "canary.txt").read_text(encoding="utf-8").strip()
    for r in bad:
        if r["success_check"]["type"] == "output_leak":
            r["notes"] = canary
    assert any("canary text" in e for e in _attack_errs(bad))


def test_attacks_reject_bad_vector_and_missing_payload(attack_rows):
    bad = copy.deepcopy(attack_rows)
    bad[0]["vector"] = "email"
    assert any("vector" in e for e in _attack_errs(bad))
    bad = copy.deepcopy(attack_rows)
    bad[0]["payload"] = "text that is not in the message"
    assert any("payload not inside" in e for e in _attack_errs(bad))


# ---- gold -----------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gold_env():
    return (
        bd.load_gold_source(),
        bd.load_kb(),
        bd.load_yaml(bd.DATA / "source" / "catalogue.yaml"),
    )


def _gold_errs(rows, docs, catalogue):
    errs: list[str] = []
    bd.check_gold(rows, docs, errs, catalogue)
    return errs


def test_gold_passes_with_the_protocol_counts(gold_env):
    rows, docs, cat = gold_env
    assert _gold_errs(rows, docs, cat) == []
    counts = bd.gold_counts(rows)
    assert counts["rows"] == 120
    assert counts["by_split"] == {"dev": 60, "test": 60}
    assert counts["by_filter_key"].keys() == {"section", "product"}  # D151


def test_gold_rejects_a_quote_that_is_not_verbatim(gold_env):
    rows, docs, cat = gold_env
    bad = copy.deepcopy(rows)
    row = next(r for r in bad if r.get("gold_evidence"))
    row["gold_evidence"][0]["quote"] = "this sentence is in no article at all"
    assert _gold_errs(bad, docs, cat) != []


def test_gold_rejects_duplicate_qid_and_bad_type(gold_env):
    rows, docs, cat = gold_env
    bad = copy.deepcopy(rows)
    bad[1]["qid"] = bad[0]["qid"]
    assert _gold_errs(bad, docs, cat) != []
    bad = copy.deepcopy(rows)
    bad[0]["type"] = "essay"
    assert _gold_errs(bad, docs, cat) != []


def test_gold_rejects_an_unknown_filter_key(gold_env):
    rows, docs, cat = gold_env
    bad = copy.deepcopy(rows)
    row = next(r for r in bad if r.get("filters"))
    row["filters"] = {"updated_after": "2026-01-01"}
    assert _gold_errs(bad, docs, cat) != []


# ---- whole runs -----------------------------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=300
    )


def test_builder_check_exits_zero():
    res = _run("scripts/build_data.py", "--check")
    assert res.returncode == 0, res.stdout[-2000:]
    assert res.stdout.strip().splitlines()[-1].startswith("OK")


def test_kit_checker_prints_ok():
    res = _run("scripts/check_project_data.py", "data")
    assert res.returncode == 0, res.stdout[-2000:]
    assert "OK" in res.stdout.splitlines()[-1]


def test_rate_card_has_return_estimate_tables():
    from milo.pricing.schema import load_rate_card

    card = load_rate_card(ROOT / "data" / "pricing" / "rate_card.v1.yaml")
    assert set(card.automated_minutes_per_request) == set(card.base_monthly_usd)
    assert set(card.eligible_share) == set(card.base_monthly_usd)
    assert all(0 < v <= 1 for v in card.eligible_share.values())
    assert all(v > 0 for v in card.automated_minutes_per_request.values())
