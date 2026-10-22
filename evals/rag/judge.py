"""LLM judge: prompt rendering, reply parsing, per-row judging, agreement with the labels, and the D26 order check.

Judge calls go through an injected callable `JudgeFn(qid, metric, prompt) -> JudgeReply`; live mode wraps a model
from `milo.llm.cloud` and `llm/retry.py`, dry mode scripts the replies. Prompts are templates with the tokens
`{{question}}`, `{{contexts}}` and `{{answer}}`.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.rag import stats

METRICS = ("faithfulness", "relevance", "context_precision")
PROMPT_NAMES = {m: f"{m}.v1.md" for m in METRICS}
PLACEHOLDER_MARK = "written after labels.jsonl"
KAPPA_MIN = 0.60  # protocol m7-judge, decision rule
CI_LOW_MIN = 0.30
UNPARSED_MAX = 0.05
LOW_PREVALENCE = 5


@dataclass
class JudgeReply:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error: str | None = None


JudgeFn = Callable[[str, str, str], JudgeReply]


# ---------------------------------------------------------------- prompts


def is_placeholder(text: str) -> bool:
    return PLACEHOLDER_MARK in text.lower()


def render(
    template: str, question: str, contexts: list[dict[str, Any]], answer: str
) -> str:
    ctx = (
        "\n\n".join(f"[{i}] {c['text']}" for i, c in enumerate(contexts, 1))
        or "(no contexts)"
    )
    return (
        template.replace("{{question}}", question)
        .replace("{{contexts}}", ctx)
        .replace("{{answer}}", answer)
    )


def parse_reply(metric: str, raw: str, n_contexts: int) -> int | list[int] | None:
    """The judge's score, or `None` when the reply is not the contract in the protocol."""
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None

    def ok(v: Any, allowed: tuple[int, ...]) -> bool:
        return isinstance(v, int) and not isinstance(v, bool) and v in allowed

    if metric == "context_precision":
        s = obj.get("scores")
        if (
            isinstance(s, list)
            and len(s) == n_contexts
            and all(ok(x, (0, 1)) for x in s)
        ):
            return [int(x) for x in s]
        return None
    v = obj.get("score")
    allowed = (0, 1) if metric == "faithfulness" else (0, 1, 2)
    return int(v) if ok(v, allowed) else None


def load_templates(prompt_dir: Path) -> dict[str, str]:
    return {
        m: (prompt_dir / n).read_text(encoding="utf-8") for m, n in PROMPT_NAMES.items()
    }


# ---------------------------------------------------------------- order check (D26)


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return out.stdout.strip() if out.returncode == 0 else ""


def order_problems(root: Path, labels: Path, prompts: list[Path]) -> list[str]:
    """The first commit of `labels` must be a proper ancestor of the last commit of every prompt file."""
    rel = lambda p: str(p.relative_to(root))  # noqa: E731
    added = _git(
        root, "log", "--diff-filter=A", "--format=%H", "--", rel(labels)
    ).splitlines()
    if not added:
        return [f"{rel(labels)} is not committed yet; the labels come first (D26)"]
    first = added[-1]
    problems: list[str] = []
    for p in prompts:
        last = _git(root, "log", "-1", "--format=%H", "--", rel(p))
        if not last:
            problems.append(f"{rel(p)} is not committed")
        elif (
            last == first
            or subprocess.run(
                ["git", "merge-base", "--is-ancestor", first, last],
                cwd=root,
                check=False,
            ).returncode
            != 0
        ):
            problems.append(f"{rel(p)} was not committed after {rel(labels)} (D26)")
    return problems


# ---------------------------------------------------------------- running


def judge_row(
    row: dict[str, Any], judge: JudgeFn, templates: dict[str, str]
) -> tuple[dict[str, Any], float]:
    out: dict[str, Any] = {}
    cost = 0.0
    for m in METRICS:
        prompt = render(templates[m], row["question"], row["contexts"], row["answer"])
        rep = judge(row["qid"], m, prompt)
        cost += rep.cost_usd
        out[m] = {
            "raw": rep.text,
            "score": parse_reply(m, rep.text, len(row["contexts"]))
            if not rep.error
            else None,
            "error": rep.error,
            "tokens_in": rep.tokens_in,
            "tokens_out": rep.tokens_out,
            "cost_usd": round(rep.cost_usd, 8),
        }
    return out, cost


def run_judges(
    rows: list[dict[str, Any]],
    judges: dict[str, JudgeFn],
    templates: dict[str, str],
    max_cost: float | None = None,
) -> bool:
    """Fill `row["judges"][slug]` in place. Returns True when the cap stopped the run before the last question."""
    spent = 0.0
    for row in rows:
        row["judges"] = {}
    for row in rows:
        if max_cost is not None and spent >= max_cost:
            for r in rows:
                if not r["judges"]:
                    r["judges"] = None
            return True
        for slug, fn in judges.items():
            out, c = judge_row(row, fn, templates)
            row["judges"][slug] = out
            spent += c
    return False


# ---------------------------------------------------------------- metrics


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _cp(v: list[int]) -> float | None:
    return sum(v) / len(v) if v else None


def _label(row: dict[str, Any], metric: str) -> Any:
    key = "context_relevance" if metric == "context_precision" else metric
    return row["labels"][key]


def _items(
    rows: list[dict[str, Any]], slug: str, metric: str
) -> tuple[list[Any], list[Any], list[dict[str, Any]]]:
    """Label and judge score per row where the judge reply parsed (and, for context precision, contexts exist)."""
    la: list[Any] = []
    ju: list[Any] = []
    used: list[dict[str, Any]] = []
    for r in rows:
        if not r.get("judges"):
            continue
        s = r["judges"][slug][metric]["score"]
        if s is None or (metric == "context_precision" and not r["contexts"]):
            continue
        la.append(_label(r, metric))
        ju.append(s)
        used.append(r)
    return la, ju, used


def agreement(rows: list[dict[str, Any]], slug: str, metric: str) -> dict[str, Any]:
    la, ju, used = _items(rows, slug, metric)
    cats = (0, 1, 2) if metric == "relevance" else (0, 1)
    if metric == "context_precision":
        groups = [(a, b) for a, b in zip(la, ju, strict=True)]
    else:
        groups = [([a], [b]) for a, b in zip(la, ju, strict=True)]
    res = stats.kappa_ci(groups, cats, weighted=metric == "relevance")
    flat_l = [x for g in groups for x in g[0]]
    minority = (
        min((flat_l.count(c) for c in (0, 1)), default=0)
        if metric != "relevance"
        else None
    )
    if metric == "relevance":
        per_q_l = [float(x) for x in la]
        per_q_j = [float(x) for x in ju]
    else:
        per_q_l = [_cp(x) if metric == "context_precision" else float(x) for x in la]
        per_q_j = [_cp(x) if metric == "context_precision" else float(x) for x in ju]
    k = res["kappa"]
    ci = res["ci"]
    res.update(
        {
            "label_mean": _mean([x for x in per_q_l if x is not None]),
            "judge_mean": _mean([x for x in per_q_j if x is not None]),
            "low_prevalence": minority is not None and minority < LOW_PREVALENCE,
            "validated": bool(
                k is not None
                and ci is not None
                and k >= KAPPA_MIN
                and ci[0] >= CI_LOW_MIN
            ),
            "undefined": k is None,
        }
    )
    return res


def by_type(rows: list[dict[str, Any]], slug: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for t in sorted({r["type"] for r in rows}):
        sub = [r for r in rows if r["type"] == t]
        entry: dict[str, Any] = {"n": len(sub)}
        for m in METRICS:
            la, ju, _ = _items(sub, slug, m)
            f = (lambda x: _cp(x)) if m == "context_precision" else (lambda x: float(x))  # noqa: E731
            lv = [f(x) for x in la if f(x) is not None]
            jv = [f(x) for x in ju if f(x) is not None]
            entry[m] = {"label_mean": _mean(lv), "judge_mean": _mean(jv)}
        out[t] = entry
    return out


def inter_judge(rows: list[dict[str, Any]], a: str, b: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in METRICS:
        groups: list[tuple[list[int], list[int]]] = []
        for r in rows:
            if not r.get("judges"):
                continue
            sa, sb = r["judges"][a][m]["score"], r["judges"][b][m]["score"]
            if (
                sa is None
                or sb is None
                or (m == "context_precision" and not r["contexts"])
            ):
                continue
            groups.append((sa, sb) if m == "context_precision" else ([sa], [sb]))
        out[m] = stats.kappa_ci(
            groups, (0, 1, 2) if m == "relevance" else (0, 1), m == "relevance"
        )
    return out


def derive(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything computed from the stored rows; replay recomputes this and compares."""
    judged = [r for r in rows if r.get("judges")]
    slugs = list(judged[0]["judges"]) if judged else []
    summary: dict[str, Any] = {}
    for slug in slugs:
        per = {m: agreement(rows, slug, m) for m in METRICS}
        replies = [r["judges"][slug][m] for r in judged for m in METRICS]
        unparsed = sum(1 for x in replies if x["score"] is None)
        rate = round(unparsed / len(replies), 4) if replies else None
        usable = bool(
            all(per[m]["validated"] for m in METRICS)
            and rate is not None
            and rate <= UNPARSED_MAX
        )
        summary[slug] = {
            "metrics": per,
            "unparsed": unparsed,
            "replies": len(replies),
            "unparsed_rate": rate,
            "usable": usable,
            "by_type": by_type(rows, slug),
            "tokens_in": sum(x["tokens_in"] for x in replies),
            "tokens_out": sum(x["tokens_out"] for x in replies),
            "cost_usd": round(sum(x["cost_usd"] for x in replies), 8),
        }
    labels: dict[str, Any] = {}
    if rows:
        labels = {
            "faithfulness": _mean([float(r["labels"]["faithfulness"]) for r in rows]),
            "relevance": _mean([float(r["labels"]["relevance"]) for r in rows]),
            "context_precision": _mean(
                [
                    v
                    for r in rows
                    if (v := _cp(r["labels"]["context_relevance"])) is not None
                ]
            ),
        }
    usable = [s for s in slugs if summary[s]["usable"]]
    if usable:
        best = max(
            usable,
            key=lambda s: (
                sum(summary[s]["metrics"][m]["kappa"] for m in METRICS),
                -slugs.index(s),
            ),
        )
        line = f"usable judge(s): {', '.join(usable)}; headline numbers from {best}"
    elif slugs:
        best = None
        line = "no usable judge: report kappas and CIs, judged numbers are not validated, hard claim not met"
    else:
        best = None
        line = "no judge ran"
    return {
        "summary": summary,
        "inter_judge": inter_judge(rows, slugs[0], slugs[1]) if len(slugs) == 2 else {},
        "label_means": labels,
        "decision": {"usable_judges": usable, "headline_judge": best, "line": line},
    }
