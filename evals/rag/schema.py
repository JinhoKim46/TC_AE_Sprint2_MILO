"""Label schema and validator for `evals/rag/labels.jsonl` (protocol m7-judge, SPEC D26).

One JSON object per line, exactly one per sampled qid:

    {"qid": "q010", "faithfulness": 1, "relevance": 2, "context_relevance": [1, 0, 0, 1, 0],
     "labeller": "<agent id>", "note": "optional"}

- `faithfulness`: 1 when every factual claim in the answer is supported by the retrieved contexts, else 0.
  An abstention makes no claim and is 1.
- `relevance`: 2 answers what was asked, 1 partly answers or declines honestly, 0 off topic or ignores the question.
- `context_relevance`: one 0 or 1 per retrieved context, in the stored order (1: the context holds information
  needed to answer). `[]` when the product retrieved nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RAG_DIR = ROOT / "evals" / "rag"
LABELS = RAG_DIR / "labels.jsonl"
TEMPLATE = RAG_DIR / "labels.template.jsonl"
FIELDS = {"qid", "faithfulness", "relevance", "context_relevance", "labeller", "note"}
REQUIRED = FIELDS - {"note"}
VALUES = {"faithfulness": (0, 1), "relevance": (0, 1, 2)}


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_row(
    row: Any, n_contexts: int | None = None, where: str = "row"
) -> list[str]:
    """Problems with one label row; empty when it is valid. `n_contexts` checks the length of `context_relevance`."""
    if not isinstance(row, dict):
        return [f"{where}: not a JSON object"]
    problems: list[str] = []
    for k in sorted(set(row) - FIELDS):
        problems.append(f"{where}: unknown field {k!r}")
    for k in sorted(REQUIRED - set(row)):
        problems.append(f"{where}: missing field {k!r}")
    if not isinstance(row.get("qid"), str) or not row.get("qid"):
        problems.append(f"{where}: qid must be a non-empty string")
    for k, allowed in VALUES.items():
        if k in row and not (_is_int(row[k]) and row[k] in allowed):
            problems.append(f"{where}: {k} must be one of {allowed}, not {row[k]!r}")
    cr = row.get("context_relevance")
    if "context_relevance" in row:
        if not isinstance(cr, list) or any(
            not (_is_int(x) and x in (0, 1)) for x in cr
        ):
            problems.append(
                f"{where}: context_relevance must be a list of 0 or 1, not {cr!r}"
            )
        elif n_contexts is not None and len(cr) != n_contexts:
            problems.append(
                f"{where}: context_relevance has {len(cr)} entries but the answer retrieved {n_contexts} contexts"
            )
    if "labeller" in row and (
        not isinstance(row["labeller"], str) or not row["labeller"].strip()
    ):
        problems.append(f"{where}: labeller must be a non-empty string")
    if "note" in row and not isinstance(row["note"], str):
        problems.append(f"{where}: note must be a string")
    return problems


def load_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            problems.append(f"line {n}: not valid JSON ({exc.msg})")
    return rows, problems


def validate_labels(
    path: Path,
    qids: list[str],
    contexts: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows and problems for a labels file against the sampled `qids` (and the context counts, when known)."""
    if not path.exists():
        return [], [f"{path.name} does not exist"]
    rows, problems = load_rows(path)
    seen: set[str] = set()
    for n, row in enumerate(rows, 1):
        qid = row.get("qid") if isinstance(row, dict) else None
        n_ctx = contexts.get(qid) if contexts and isinstance(qid, str) else None
        problems += validate_row(row, n_ctx, f"line {n} ({qid})")
        if isinstance(qid, str):
            if qid in seen:
                problems.append(f"line {n}: duplicate qid {qid}")
            seen.add(qid)
            if qid not in qids:
                problems.append(f"line {n}: qid {qid} is not in the sample")
    for q in qids:
        if q not in seen:
            problems.append(f"missing label for sampled qid {q}")
    return rows, problems


def template_rows(
    qids: list[str], contexts: dict[str, int] | None = None
) -> list[dict[str, Any]]:
    return [
        {
            "qid": q,
            "faithfulness": None,
            "relevance": None,
            "context_relevance": [None] * (contexts.get(q, 0) if contexts else 0),
            "labeller": "",
            "note": "",
        }
        for q in qids
    ]
