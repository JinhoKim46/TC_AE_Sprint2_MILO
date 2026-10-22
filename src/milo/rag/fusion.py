"""Reciprocal rank fusion (SPEC D8): score(d) = sum over lists of 1 / (k + rank(d)), rank from 1, k = 60.

Pure and deterministic: ties are broken by the best rank any list gave the chunk, then by the chunk id.
"""

from __future__ import annotations

from collections.abc import Sequence

RRF_K = 60


def rrf(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Fuse ranked id lists (best first). A chunk missing from a list gets nothing from it. Best first."""
    if k < 0:
        raise ValueError("k must not be negative")
    score: dict[str, float] = {}
    best: dict[str, int] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for pos, cid in enumerate(ranking, start=1):
            if cid in seen:  # a repeated id in one list counts once, at its first rank
                continue
            seen.add(cid)
            score[cid] = score.get(cid, 0.0) + 1.0 / (k + pos)
            best[cid] = min(best.get(cid, pos), pos)
    return sorted(score.items(), key=lambda kv: (-kv[1], best[kv[0]], kv[0]))
