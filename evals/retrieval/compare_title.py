"""kb-edit-v1 decision: paired comparison of the title-prefix arm (B) against no prefix (A) on dev.

    python evals/retrieval/compare_title.py            write evals/results/retrieval/kb_edit_title.json
    python evals/retrieval/compare_title.py --check    recompute and compare with the stored file

Offline: reads the two stored retrieval results only. The rule is in evals/protocols/kb-edit-v1.md (D24): the
prefix stays only when the paired CI of accepted nDCG@5 (B minus A) lies above zero.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "evals" / "results" / "retrieval"
ARM_A = (
    RESULTS / "retrieval_dev.json"
)  # no prefix: the product setting after this decision
ARM_B = RESULTS / "retrieval_dev_title.json"
OUT = RESULTS / "kb_edit_title.json"
METRICS = ("ndcg5", "recall5", "mrr")


def _ab() -> Any:
    spec = importlib.util.spec_from_file_location("ab", ROOT / "evals/retrieval/ab.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ab"] = mod
    spec.loader.exec_module(mod)
    return mod


def per_row(result: dict[str, Any], key: str) -> dict[str, dict[str, float]]:
    return {r["qid"]: r[key] for r in result["rows"] if r["answerable"]}


def compare() -> dict[str, Any]:
    ab = _ab()
    a = json.loads(ARM_A.read_text(encoding="utf-8"))
    b = json.loads(ARM_B.read_text(encoding="utf-8"))
    assert not a["title_prefix"] and b["title_prefix"], "arm files swapped"
    out: dict[str, Any] = {"arm_a": ARM_A.name, "arm_b": ARM_B.name}
    for key in ("metrics_accepted", "metrics"):
        ra, rb = per_row(a, key), per_row(b, key)
        assert ra.keys() == rb.keys()
        qids = sorted(ra)
        draws = ab.paired_draws(len(qids))
        block: dict[str, Any] = {"n": len(qids)}
        for m in METRICS:
            diffs = [rb[q][m] - ra[q][m] for q in qids]
            block[m] = {
                "a": round(sum(ra[q][m] for q in qids) / len(qids), 4),
                "b": round(sum(rb[q][m] for q in qids) / len(qids), 4),
                "mean_diff": round(sum(diffs) / len(diffs), 4),
                "ci": ab.paired_ci(diffs, draws),
                "better": sum(d > 0 for d in diffs),
                "worse": sum(d < 0 for d in diffs),
            }
        out["accepted" if key == "metrics_accepted" else "strict"] = block
    lo = out["accepted"]["ndcg5"]["ci"][0]
    out["decision"] = "keep_title_prefix" if lo > 0 else "drop_title_prefix"
    out["rule"] = (
        "keep only when the paired CI lower bound of accepted nDCG@5 (B minus A) is above 0 (D24)"
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--check", action="store_true", help="compare with the stored file")
    args = ap.parse_args(argv)
    fresh = compare()
    if args.check:
        same = json.loads(OUT.read_text(encoding="utf-8")) == fresh
        print(f"{'MATCH' if same else 'MISMATCH'} {OUT.relative_to(ROOT)}")
        return 0 if same else 1
    OUT.write_text(json.dumps(fresh, indent=2) + "\n", encoding="utf-8")
    acc = fresh["accepted"]["ndcg5"]
    print(
        f"accepted nDCG@5 A {acc['a']}  B {acc['b']}  diff {acc['mean_diff']} CI {acc['ci']}  -> {fresh['decision']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
