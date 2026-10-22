"""The M7 sample: 30 dev questions, stratified by `type`, drawn with a fixed seed (protocol m7-judge, SPEC D26).

Rows come from `evals.common.load_gold("dev")` only. A row whose split is not `dev` stops the run.
"""

from __future__ import annotations

import random
from typing import Any

from evals import common

N = 30
SEED = 26


def allocate(counts: dict[str, int], n: int) -> dict[str, int]:
    """Proportional allocation by the largest-remainder rule; ties go to the type name in sorted order."""
    total = sum(counts.values())
    if n > total:
        raise ValueError(f"cannot sample {n} from {total} rows")
    exact = {t: n * c / total for t, c in counts.items()}
    alloc = {t: int(x) for t, x in exact.items()}
    left = n - sum(alloc.values())
    for t in sorted(counts, key=lambda t: (-(exact[t] - alloc[t]), t))[:left]:
        alloc[t] += 1
    return alloc


def draw(
    rows: list[dict[str, Any]], n: int = N, seed: int = SEED
) -> list[dict[str, Any]]:
    """`n` rows (qid, type, question), sorted by qid. Same rows and seed give the same sample."""
    bad = [r["qid"] for r in rows if r.get("split") != "dev"]
    if bad:
        raise PermissionError(f"the sample is dev only; non-dev rows: {bad[:5]}")
    by_type: dict[str, list[dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: r["qid"]):
        by_type.setdefault(r["type"], []).append(r)
    alloc = allocate({t: len(v) for t, v in by_type.items()}, n)
    rng = random.Random(seed)
    picked: list[dict[str, Any]] = []
    for t in sorted(by_type):
        picked += rng.sample(by_type[t], alloc[t])
    return [
        {"qid": r["qid"], "type": r["type"], "question": r["question"]}
        for r in sorted(picked, key=lambda r: r["qid"])
    ]


def build() -> dict[str, Any]:
    dev = common.load_gold("dev")
    rows = draw(dev)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    return {
        "split": "dev",
        "seed": SEED,
        "n": len(rows),
        "dev_rows": len(dev),
        "strata": dict(sorted(counts.items())),
        "gold_sha256": common.gold_sha256(),
        "rows": rows,
    }
