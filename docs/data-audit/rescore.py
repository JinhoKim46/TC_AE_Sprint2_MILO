"""Offline re-score of the stored live dev rows of the discovery evaluation (data audit).

No model call, no network, no `milo` import. Reads only:
  evals/results/discovery/discovery_live.json   (dev rows A-1, B-1, E-1)
  data/demo/conversations/{A-1,B-1,E-1}.expected.json
  data/demo/conversations/{A-1,B-1,E-1}.audited.expected.json
The sealed test conversations and their results are never opened.

Scenarios
  (a) original expected files, original scorer (must reproduce the stored pooled F1)
  (b) audited expected files, original scorer
  (c) audited expected files, proposed scorer (docs/data-audit/conversations-dev.md, "Scoring proposal")

Run from the project root:  python docs/data-audit/rescore.py [--items]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "evals" / "results" / "discovery" / "discovery_live.json"
CONV = ROOT / "data" / "demo" / "conversations"
DEV = ("A-1", "B-1", "E-1")

# Field types from protocols/discovery.v1.yaml (copied so the script needs no YAML parser).
LIST_FIELDS = {
    "request_channels",
    "tools_in_use",
    "steps_in_process",
    "approval_steps",
    "pain_points",
    "automation_goals",
    "personal_data_types",
    "urgent_request_types",
}
LIST_ENUM_FIELDS = {"error_types", "integrations_available"}
# string fields that hold a description (compared with the item rule); the others are identifiers (exact)
DESCRIPTIVE_STRINGS = {"industry", "owner_role", "start_timeline", "budget_range"}


# ---------------------------------------------------------------- original scorer (run_discovery.py, verbatim logic)


def norm_text(text: str) -> str:
    return " ".join(str(text).split())


def norm_key(text: Any) -> str:
    return norm_text(str(text)).casefold()


def _norm(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        return norm_key(value)
    if isinstance(value, list):
        items = [_norm(v) for v in value]
        if all(isinstance(i, str) for i in items):
            return sorted(set(items))
        return items
    return value


def fact_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    if (
        expected["field_id"] != actual["field_id"]
        or expected["status"] != actual["status"]
    ):
        return False
    if _norm(expected["value"]) != _norm(actual["value"]):
        return False
    for key in ("unit", "period"):
        if expected.get(key) is not None and expected[key] != actual.get(key):
            return False
    return True


def score_original(
    rows: list[dict[str, Any]], expected: dict[tuple[str, int], list]
) -> dict[str, int]:
    tp = n_exp = n_act = 0
    for r in rows:
        exp = expected[(r["conversation"], r["turn"])]
        act = r["actual"]["facts"]
        tp += sum(1 for e in exp if any(fact_matches(e, a) for a in act))
        n_exp += len(exp)
        n_act += len(act)
    return {"tp_exp": tp, "tp_act": tp, "n_exp": n_exp, "n_act": n_act}


# ---------------------------------------------------------------- proposed scorer

STOPWORDS = frozenset(
    "a an the and or of to in into on at by for with it its our we us i me my myself all get gets "
    "then them those that this is are be only just so there".split()
)
SYNONYMS = {"dispatch": ["send"], "nobody": ["no", "one"]}


def _stem(tok: str) -> str:
    if tok.endswith("ies") and len(tok) > 4:
        return tok[:-3] + "y"
    for suf in ("ing", "ed"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            return tok[: -len(suf)]
    if tok.endswith("s") and not tok.endswith("ss") and len(tok) - 1 >= 3:
        return tok[:-1]
    return tok


def item_tokens(item: Any) -> frozenset[str]:
    """T(s): casefold, every non [a-z0-9] char to a space, split, drop stopwords, synonyms, light stemming."""
    words = re.sub(r"[^a-z0-9]+", " ", str(item).casefold()).split()
    out: list[str] = []
    for w in words:
        if w in STOPWORDS:
            continue
        for s in SYNONYMS.get(w, [w]):
            out.append(_stem(s))
    return frozenset(out)


def item_match(x: Any, y: Any) -> bool:
    a, b = item_tokens(x), item_tokens(y)
    if not a or not b:
        return norm_key(x) == norm_key(y)
    return a <= b or b <= a or len(a & b) / len(a | b) >= 0.5


def list_match(expected_items: list, actual_items: list) -> bool:
    """Every expected item is matched by some actual item and every actual item matches some expected item."""
    if not expected_items or not actual_items:
        return not expected_items and not actual_items
    return all(
        any(item_match(e, a) for a in actual_items) for e in expected_items
    ) and all(any(item_match(e, a) for e in expected_items) for a in actual_items)


def value_match(fid: str, ev: Any, av: Any, status: str) -> bool:
    if status in ("conflicting", "unknown"):
        return _norm(ev) == _norm(av)
    if fid in LIST_FIELDS:
        return isinstance(av, list) and list_match(list(ev), list(av))
    if fid in LIST_ENUM_FIELDS:
        return isinstance(av, list) and {norm_key(i) for i in ev} == {
            norm_key(i) for i in av
        }
    if fid in DESCRIPTIVE_STRINGS and isinstance(ev, str) and isinstance(av, str):
        return item_match(ev, av)
    return _norm(ev) == _norm(av)


def proposed_match(e: dict[str, Any], e_value: Any, a: dict[str, Any]) -> bool:
    if e["field_id"] != a["field_id"] or e["status"] != a["status"]:
        return False
    if not value_match(e["field_id"], e_value, a["value"], e["status"]):
        return False
    for key in ("unit", "period"):
        if e.get(key) is not None and e[key] != a.get(key):
            return False
    return True


def score_proposed(
    rows: list[dict[str, Any]],
    expected: dict[tuple[str, int], list],
    trace: list | None = None,
) -> dict[str, int]:
    """Rule P1 to P4 of the proposal.

    P1 list fields are compared cumulatively: the merge accumulates list items, so the recorded value at turn t
       is compared with the union of the expected items for that field up to turn t.
    P2 items match by the item rule (item_match); list_enum fields by exact set.
    P3 an expected fact with no recorded fact for its field in that turn is still matched when the store already
       holds a matching value from an earlier turn ("already recorded": the merge emits no event for a restatement).
    P4 precision counts recorded facts that match an expected fact of the same turn; recall counts expected facts
       matched by P2 or P3.
    """
    tp_exp = tp_act = n_exp = n_act = 0
    store: dict[tuple[str, str], dict[str, Any]] = {}
    exp_state: dict[tuple[str, str], Any] = {}
    for r in rows:
        conv = r["conversation"]
        exp = expected[(conv, r["turn"])]
        act = r["actual"]["facts"]
        for a in act:
            store[(conv, a["field_id"])] = a
        cum: dict[str, Any] = {}
        for e in exp:
            fid = e["field_id"]
            if (
                fid in LIST_FIELDS
                and e["status"] == "reported"
                and isinstance(e["value"], list)
            ):
                prev = exp_state.get((conv, fid)) or []
                merged = list(prev) + [
                    i
                    for i in e["value"]
                    if norm_key(i) not in {norm_key(p) for p in prev}
                ]
                exp_state[(conv, fid)] = merged
                cum[fid] = merged
            else:
                exp_state[(conv, fid)] = e["value"]
                cum[fid] = e["value"]
        act_ok = [
            any(proposed_match(e, cum[e["field_id"]], a) for e in exp) for a in act
        ]
        for e in exp:
            fid = e["field_id"]
            hit = any(proposed_match(e, cum[fid], a) for a in act)
            how = "turn" if hit else ""
            if not hit and not any(a["field_id"] == fid for a in act):
                held = store.get((conv, fid))
                if held is not None and proposed_match(e, cum[fid], held):
                    hit, how = True, "already_recorded"
            tp_exp += hit
            if trace is not None:
                trace.append((conv, r["turn"], "expected", fid, hit, how))
        for a, ok in zip(act, act_ok):
            if trace is not None:
                trace.append((conv, r["turn"], "recorded", a["field_id"], ok, ""))
        tp_act += sum(act_ok)
        n_exp += len(exp)
        n_act += len(act)
    return {"tp_exp": tp_exp, "tp_act": tp_act, "n_exp": n_exp, "n_act": n_act}


# ---------------------------------------------------------------- driver


def prf(c: dict[str, int]) -> dict[str, float | None]:
    p = round(c["tp_act"] / c["n_act"], 4) if c["n_act"] else None
    r = round(c["tp_exp"] / c["n_exp"], 4) if c["n_exp"] else None
    f = round(2 * p * r / (p + r), 4) if p and r else None
    return {"precision": p, "recall": r, "f1": f, **c}


def load_expected(suffix: str) -> dict[tuple[str, int], list]:
    out = {}
    for conv in DEV:  # never a glob: the sealed test files sit in the same folder
        doc = json.loads((CONV / f"{conv}{suffix}").read_text(encoding="utf-8"))
        for t in doc["turns"]:
            out[(conv, int(t["turn"]))] = t["facts"]
    return out


def main(argv: list[str]) -> int:
    live = json.loads(LIVE.read_text(encoding="utf-8"))
    rows = [r for r in live["rows"] if r["conversation"] in DEV]
    original = load_expected(".expected.json")
    audited = load_expected(".audited.expected.json")
    for r in rows:  # the rows carry the expected facts they were scored with; they must equal the files
        assert r["expected"]["facts"] == original[(r["conversation"], r["turn"])], (
            r["conversation"],
            r["turn"],
        )
    scenarios = {
        "a_original_files_original_scorer": lambda rs: score_original(rs, original),
        "b_audited_files_original_scorer": lambda rs: score_original(rs, audited),
        "c_audited_files_proposed_scorer": lambda rs: score_proposed(rs, audited),
        "c0_original_files_proposed_scorer": lambda rs: score_proposed(rs, original),
    }
    result: dict[str, Any] = {}
    for name, fn in scenarios.items():
        result[name] = {"pooled": prf(fn(rows))}
        for conv in DEV:
            result[name][conv] = prf(fn([r for r in rows if r["conversation"] == conv]))
    stored = live["metrics"]["pooled"]["fact_f1"]
    got = result["a_original_files_original_scorer"]["pooled"]["f1"]
    result["check"] = {
        "stored_pooled_f1": stored,
        "recomputed_a": got,
        "ok": stored == got,
    }
    print(json.dumps(result, indent=1))
    if "--items" in argv:
        trace: list = []
        score_proposed(rows, audited, trace)
        for t in trace:
            if not t[4] or t[5] == "already_recorded":
                print(*t, sep="\t")
    return 0 if stored == got else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
