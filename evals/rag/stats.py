"""Cohen's kappa (unweighted and quadratic-weighted) with a question-level bootstrap CI (SPEC D24).

Items are grouped by question so that context precision, which has several items per question, resamples whole
questions. 2000 resamples, seed 0, percentile 95 percent interval.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

BOOT = 2000
SEED = 0


def kappa(
    a: Sequence[int],
    b: Sequence[int],
    categories: Sequence[int],
    weighted: bool = False,
) -> float | None:
    """Cohen's kappa of two label lists. `None` when it is undefined (no items, or chance agreement is total)."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    k = len(categories)
    idx = {c: i for i, c in enumerate(categories)}
    obs = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b, strict=True):
        obs[idx[x]][idx[y]] += 1.0 / n
    row = [sum(r) for r in obs]
    col = [sum(obs[i][j] for i in range(k)) for j in range(k)]

    def w(i: int, j: int) -> float:
        if not weighted:
            return 0.0 if i == j else 1.0
        return ((i - j) / (k - 1)) ** 2 if k > 1 else 0.0

    num = sum(w(i, j) * obs[i][j] for i in range(k) for j in range(k))
    den = sum(w(i, j) * row[i] * col[j] for i in range(k) for j in range(k))
    if den == 0:
        return None
    return 1.0 - num / den


def draws(n: int) -> list[list[int]]:
    rng = random.Random(SEED)
    return [[rng.randrange(n) for _ in range(n)] for _ in range(BOOT)]


def kappa_ci(
    groups: Sequence[tuple[Sequence[int], Sequence[int]]],
    categories: Sequence[int],
    weighted: bool = False,
) -> dict[str, object]:
    """Kappa over the pooled items of `groups` (one group per question), with the bootstrap CI over groups."""
    flat_a = [x for ga, _ in groups for x in ga]
    flat_b = [y for _, gb in groups for y in gb]
    point = kappa(flat_a, flat_b, categories, weighted)
    ks: list[float] = []
    for d in draws(len(groups)) if groups else []:
        pa = [x for i in d for x in groups[i][0]]
        pb = [y for i in d for y in groups[i][1]]
        k = kappa(pa, pb, categories, weighted)
        if k is not None:
            ks.append(k)
    ks.sort()
    ci: list[float] | None = None
    if ks:
        ci = [
            round(ks[int(0.025 * len(ks))], 4),
            round(ks[int(0.975 * len(ks)) - 1], 4),
        ]
    agree = (
        sum(1 for x, y in zip(flat_a, flat_b, strict=True) if x == y) / len(flat_a)
        if flat_a
        else None
    )
    return {
        "kappa": None if point is None else round(point, 4),
        "ci": ci,
        "ci_resamples_defined": len(ks),
        "n_questions": len(groups),
        "n_items": len(flat_a),
        "agreement": None if agree is None else round(agree, 4),
        "weighted": weighted,
    }
