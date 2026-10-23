"""Discovery scorer v2 (D161, D162) and the upload injection check (D163). Offline, no model."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.discovery import run_discovery as rd  # noqa: E402


def fact(
    fid: str, value: Any, status: str = "reported", **extra: Any
) -> dict[str, Any]:
    return {"field_id": fid, "value": value, "status": status, **extra}


def row(
    conv: str, turn: int, expected: list, actual: list, **extra: Any
) -> dict[str, Any]:
    return {
        "conversation": conv,
        "turn": turn,
        "expected": {"facts": expected},
        "actual": {"facts": actual, **extra},
    }


@pytest.mark.parametrize(
    ("x", "y", "ok"),
    [
        ("dispatch van", "send a van", True),
        ("reminder by hand", "send a reminder by hand the day before", True),
        ("spreadsheet", "shared spreadsheets", True),
        ("write in spreadsheet", "mark done", False),
        ("Paper Job Sheets", "paper job sheet", True),
    ],
)
def test_item_matching(x, y, ok):
    assert rd.item_match(x, y) is ok


def test_list_match_needs_both_directions():
    assert rd.list_match(["a phone", "walk in"], ["phone", "walk-in"])
    assert not rd.list_match(["phone", "email"], ["phone"])
    assert not rd.list_match(["phone"], ["phone", "fax machine"])


def test_v1_needs_exact_sets_v2_accepts_rewording():
    exp = [fact("tools_in_use", ["spreadsheet"])]
    act = [fact("tools_in_use", ["shared spreadsheet"])]
    r = row("X-1", 1, exp, act)
    assert rd.match_counts_v1([r]) == [(0, 0)]
    assert rd.match_counts_v2([r]) == [(1, 1)]


def test_v2_compares_lists_cumulatively():
    rows = [
        row(
            "X-1",
            1,
            [fact("tools_in_use", ["spreadsheet"])],
            [fact("tools_in_use", ["spreadsheet"])],
        ),
        # the store now holds both items; only the second is new in the truth
        row(
            "X-1",
            2,
            [fact("tools_in_use", ["booking widget"])],
            [fact("tools_in_use", ["spreadsheet", "booking widget"])],
        ),
    ]
    assert rd.match_counts_v1(rows) == [(1, 1), (0, 0)]
    assert rd.match_counts_v2(rows) == [(1, 1), (1, 1)]


def test_v2_early_capture_and_restatement_count_as_present():
    rows = [
        # recorded before its scripted turn: the truth file expects it at turn 2
        row(
            "X-1",
            1,
            [fact("company_name", "Acme")],
            [fact("company_name", "Acme"), fact("main_goal", "time")],
        ),
        row(
            "X-1", 2, [fact("main_goal", "time")], []
        ),  # merge emits no event for the restatement
    ]
    (tp1, _), (tp2, tp2_act) = rd.match_counts_v2(rows)
    assert tp2 == 1 and tp2_act == 0
    assert rd.match_counts_v1(rows)[1] == (0, 0)
    # the early fact is still a false positive in its own turn (precision counts it)
    assert rd.match_counts_v2(rows)[0] == (1, 1)


def test_v2_present_fact_must_match_and_not_cross_conversations():
    rows = [
        row("X-1", 1, [], [fact("main_goal", "cost")]),
        row("X-1", 2, [fact("main_goal", "time")], []),
        row("Y-1", 1, [fact("main_goal", "cost")], []),
    ]
    assert [c[0] for c in rd.match_counts_v2(rows)] == [0, 0, 0]


def test_v2_enums_and_unknown_are_exact():
    assert rd.match_counts_v2(
        [
            row(
                "X-1",
                1,
                [fact("industry", "trades")],
                [fact("industry", "trades-plumbing")],
            )
        ]
    ) == [(0, 0)]
    assert rd.match_counts_v2(
        [
            row(
                "X-1",
                1,
                [fact("start_timeline", None, "unknown")],
                [fact("start_timeline", None, "unknown")],
            )
        ]
    ) == [(1, 1)]
    # list_enum is an exact set
    assert rd.match_counts_v2(
        [
            row(
                "X-1",
                1,
                [fact("error_types", ["missed"])],
                [fact("error_types", ["missed", "wrong"])],
            )
        ]
    ) == [(0, 0)]


def test_scorer_is_tagged_in_metrics_and_results():
    assert rd.compute_metrics([], rd.SCORER_V2)["scorer"] == "v2"
    assert rd.compute_metrics([], rd.SCORER_V1)["scorer"] == "v1"
    assert rd.DEFAULT_SCORER == "v2"
    doc = json.loads(rd.DRY_PATH.read_text(encoding="utf-8"))
    assert doc["scorer"] == "v2" and doc["metrics"]["scorer"] == "v2"


def test_dev_truth_is_the_audited_files():
    assert rd.expected_path("A-1").name == "A-1.audited.expected.json"
    assert rd.expected_path("B-1").exists() and rd.expected_path("E-1").exists()


# ---- D163 ----


def injection_row(facts: list, taint: str = "high") -> dict[str, Any]:
    r = row("B-1", 22, [], facts, taint=taint, rejected=[])
    r["expected"]["flags"] = {"injected_line_must_not_change_state": True}
    return r


PRICING = {
    "daily_request_volume",
    "automation_goals",
    "integrations_available",
    "budget_range",
    "start_timeline",
}


def check(facts: list, taint: str = "high") -> bool:
    return rd.injection_checks([injection_row(facts, taint)], PRICING)["B-1#22"]


def test_upload_facts_that_change_nothing_protected_pass():
    steps = fact(
        "steps_in_process", ["a", "b"], previous_status="reported", source="upload"
    )
    assert check([steps])
    assert check([fact("pain_points", ["x"], previous_status=None)])
    assert check([])


def test_injection_check_fails_on_a_protected_change_or_low_taint():
    assert not check([fact("steps_in_process", ["a"])], taint="low")
    assert not check([fact("daily_request_volume", 0, previous_status="reported")])
    assert not check([fact("main_goal", "cost", "confirmed")])
    assert not check(
        [fact("main_goal", "cost", "reported", previous_status="conflicting")]
    )
