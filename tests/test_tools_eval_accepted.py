"""data-v2 fix cycle 1: accepted alternatives and resolved cross-owner outcome in evals/agent/run_tools.py."""

from __future__ import annotations

import json

from evals.agent import run_tools as rt


def _case(cid: str) -> dict:
    rows = [
        json.loads(line)
        for line in rt.CASES_PATH.read_text(encoding="utf-8").splitlines()
    ]
    return next(r for r in rows if r["id"] == cid)


def _run(cid: str, calls: list[dict]) -> dict:
    case = _case(cid)
    row = rt.run_case(case, rt.ScriptedExpected(calls), rt.system_prompt())
    row.update(rt.score_row(row))
    return row


def test_cross_owner_expected_call_resolves_to_not_found_without_state_change() -> None:
    row = _run("tc21", _case("tc21")["expected_calls"])
    assert row["observed_outcome"] == "not_found"
    assert row["backend_state_changed"] is False
    assert row["matched"] == "expected" and row["passed"] is True


def test_cross_owner_refusal_is_an_accepted_alternative() -> None:
    row = _run("tc21", [])
    assert row["matched"] == "accepted:0" and row["passed"] is True


def test_own_submission_stays_at_needs_approval() -> None:
    row = _run("tc07", _case("tc07")["expected_calls"])
    assert row["observed_outcome"] == "needs_approval" and row["passed"] is True


def test_wrong_outcome_fails_the_row() -> None:
    row = _run("tc21", _case("tc21")["expected_calls"])
    row["observed_outcome"] = "withdrawn"
    assert rt.score_row(row)["passed"] is False


def test_accepted_alternative_with_other_calls_is_recorded() -> None:
    case = _case("tc31")
    alt = case["accepted"][0]["expected_calls"]
    row = _run("tc31", alt)
    assert row["matched"] == "accepted:0" and row["passed"] is True


def test_unrelated_calls_match_nothing() -> None:
    row = _run(
        "tc20", [{"name": "estimate_cost", "args": {"module_ids": ["email-triage"]}}]
    )
    assert row["matched"] is None and row["passed"] is False
