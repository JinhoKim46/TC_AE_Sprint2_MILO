#!/usr/bin/env python3
"""Check a project's data folder (M0 checkpoint). Standard library only. Offline.

    python scripts/check_project_data.py <data_dir>              # full check
    python scripts/check_project_data.py templates --templates   # schema only, for the kit templates

Layout of <data_dir>:
    kb/**/*.md          at least 20 documents; each starts with a front-matter block (`---` ... `---`)
                        holding `title`, `section` and `updated`
    gold.jsonl          at least 30 questions: >= 6 unanswerable, >= 6 identifier, >= 4 needing a filter
    tool_cases.jsonl    at least 15 cases, covering at least 3 distinct tools
    attacks.jsonl       at least 10 attacks
Quotes in gold evidence must appear verbatim (whitespace and case normalised) in the named document.
Exit code 0 when clean, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MIN_DOCS, MIN_GOLD, MIN_UNANS, MIN_IDENT, MIN_FILTER, MIN_TOOLS, MIN_ATTACKS = 20, 30, 6, 6, 4, 15, 10
MIN_DISTINCT_TOOLS = 3
KB_META = ("title", "section", "updated")
GOLD_TYPES = {"identifier", "conceptual", "paraphrase", "multi_hop", "unanswerable"}
ABSTAIN = "INSUFFICIENT_CONTEXT"
VECTORS = {"direct", "rag", "tool_output"}
CARRIERS = {"user_message", "kb_chunk", "tool_output"}
MATCHES = {"exact", "names"}
SPLITS = {"dev", "test"}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def read_jsonl(path: Path, errs: list[str]) -> list[dict]:
    if not path.exists():
        errs.append(f"{path.name}: missing")
        return []
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            errs.append(f"{path.name}:{n}: invalid JSON ({e.msg})")
            continue
        if not isinstance(row, dict):
            errs.append(f"{path.name}:{n}: row is not an object")
            continue
        rows.append(row)
    return rows


def load_kb(kb: Path, errs: list[str]) -> dict[str, tuple[dict, str]]:
    docs: dict[str, tuple[dict, str]] = {}
    for p in sorted(kb.rglob("*.md")):
        doc_id = p.relative_to(kb).with_suffix("").as_posix()
        text = p.read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
        if not m:
            errs.append(f"kb/{doc_id}: no front-matter block")
            docs[doc_id] = ({}, text)
            continue
        meta = {}
        for line in m.group(1).splitlines():
            k, sep, v = line.partition(":")
            if sep:
                meta[k.strip()] = v.strip()
        for k in KB_META:
            if not meta.get(k):
                errs.append(f"kb/{doc_id}: front matter lacks `{k}`")
        docs[doc_id] = (meta, m.group(2))
    return docs


def need(row: dict, key: str, typ, where: str, errs: list[str]) -> bool:
    if key not in row or not isinstance(row[key], typ) or (isinstance(row[key], str) and not row[key].strip()):
        errs.append(f"{where}: `{key}` missing or not {typ.__name__ if isinstance(typ, type) else typ}")
        return False
    return True


def check_gold(rows, docs, errs, templates):
    seen = set()
    for r in rows:
        w = f"gold.jsonl:{r.get('qid', '?')}"
        need(r, "qid", str, w, errs)
        if r.get("qid") in seen:
            errs.append(f"{w}: duplicate qid")
        seen.add(r.get("qid"))
        need(r, "question", str, w, errs)
        need(r, "reference_answer", str, w, errs)
        need(r, "gold_evidence", list, w, errs)
        if r.get("type") not in GOLD_TYPES:
            errs.append(f"{w}: `type` must be one of {sorted(GOLD_TYPES)}")
        if r.get("split") not in SPLITS:
            errs.append(f"{w}: `split` must be dev or test")
        if r.get("filters") is not None and not isinstance(r["filters"], dict):
            errs.append(f"{w}: `filters` must be null or an object")
        ev = r.get("gold_evidence") if isinstance(r.get("gold_evidence"), list) else []
        if r.get("type") == "unanswerable":
            if ev or r.get("reference_answer") != ABSTAIN:
                errs.append(f"{w}: unanswerable needs gold_evidence [] and reference_answer {ABSTAIN}")
        elif not ev:
            errs.append(f"{w}: answerable question has no gold_evidence")
        for e in ev:
            if not (isinstance(e, dict) and isinstance(e.get("doc_id"), str) and isinstance(e.get("quote"), str)):
                errs.append(f"{w}: evidence items need string `doc_id` and `quote`")
                continue
            if templates:
                continue
            if e["doc_id"] not in docs:
                errs.append(f"{w}: unknown doc_id {e['doc_id']}")
            elif norm(e["quote"]) not in norm(docs[e["doc_id"]][1]):
                errs.append(f"{w}: quote is not verbatim in {e['doc_id']}")
            elif isinstance(r.get("filters"), dict):
                meta = docs[e["doc_id"]][0]
                for k, v in r["filters"].items():
                    if meta.get(k) != str(v):
                        errs.append(f"{w}: filter {k}={v!r} does not match {e['doc_id']} metadata")
    if not templates:
        n = lambda f: sum(1 for r in rows if f(r))  # noqa: E731
        for label, got, want in (
            ("questions", len(rows), MIN_GOLD),
            ("unanswerable questions", n(lambda r: r.get("type") == "unanswerable"), MIN_UNANS),
            ("identifier questions", n(lambda r: r.get("type") == "identifier"), MIN_IDENT),
            ("questions needing a filter", n(lambda r: isinstance(r.get("filters"), dict) and r["filters"]), MIN_FILTER),
        ):
            if got < want:
                errs.append(f"gold.jsonl: {got} {label}, need at least {want}")
        splits = {r.get("split") for r in rows}
        if splits != SPLITS:
            errs.append("gold.jsonl: both `dev` and `test` splits are needed")


def check_tools(rows, errs, templates):
    seen, names = set(), set()
    for r in rows:
        w = f"tool_cases.jsonl:{r.get('id', '?')}"
        need(r, "id", str, w, errs)
        if r.get("id") in seen:
            errs.append(f"{w}: duplicate id")
        seen.add(r.get("id"))
        need(r, "user_id", str, w, errs)
        need(r, "query", str, w, errs)
        need(r, "expected_calls", list, w, errs)
        if r.get("match") not in MATCHES:
            errs.append(f"{w}: `match` must be one of {sorted(MATCHES)}")
        for c in r.get("expected_calls") if isinstance(r.get("expected_calls"), list) else []:
            if not (isinstance(c, dict) and isinstance(c.get("name"), str) and isinstance(c.get("args"), dict)):
                errs.append(f"{w}: expected_calls items need `name` and `args` (object)")
            else:
                names.add(c["name"])
    if not templates:
        if len(rows) < MIN_TOOLS:
            errs.append(f"tool_cases.jsonl: {len(rows)} cases, need at least {MIN_TOOLS}")
        if len(names) < MIN_DISTINCT_TOOLS:
            errs.append(f"tool_cases.jsonl: {len(names)} distinct tools expected, need at least {MIN_DISTINCT_TOOLS}")
        if not any(not r.get("expected_calls") for r in rows):
            errs.append("tool_cases.jsonl: add at least one case where no tool should be called (over-eager check)")


def check_attacks(rows, errs, templates):
    seen = set()
    for r in rows:
        w = f"attacks.jsonl:{r.get('id', '?')}"
        need(r, "id", str, w, errs)
        if r.get("id") in seen:
            errs.append(f"{w}: duplicate id")
        seen.add(r.get("id"))
        for k in ("goal", "user_id", "user_message", "payload"):
            need(r, k, str, w, errs)
        if r.get("vector") not in VECTORS:
            errs.append(f"{w}: `vector` must be one of {sorted(VECTORS)}")
        if r.get("carrier") not in CARRIERS:
            errs.append(f"{w}: `carrier` must be one of {sorted(CARRIERS)}")
        sc = r.get("success_check")
        if not (isinstance(sc, dict) and isinstance(sc.get("type"), str)):
            errs.append(f"{w}: `success_check` needs a `type`")
        if r.get("carrier") == "user_message" and isinstance(r.get("payload"), str) \
                and r["payload"] not in r.get("user_message", ""):
            errs.append(f"{w}: carrier is user_message but payload is not inside user_message")
    if not templates and len(rows) < MIN_ATTACKS:
        errs.append(f"attacks.jsonl: {len(rows)} attacks, need at least {MIN_ATTACKS}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--templates", action="store_true", help="schema checks only: no KB, no minimum counts")
    a = ap.parse_args(argv)
    errs: list[str] = []
    docs: dict = {}
    if not a.templates:
        kb = a.data_dir / "kb"
        if not kb.is_dir():
            errs.append("kb/: missing")
        else:
            docs = load_kb(kb, errs)
            if len(docs) < MIN_DOCS:
                errs.append(f"kb/: {len(docs)} documents, need at least {MIN_DOCS}")
    check_gold(read_jsonl(a.data_dir / "gold.jsonl", errs), docs, errs, a.templates)
    check_tools(read_jsonl(a.data_dir / "tool_cases.jsonl", errs), errs, a.templates)
    check_attacks(read_jsonl(a.data_dir / "attacks.jsonl", errs), errs, a.templates)
    for e in errs:
        print(f"FAIL {e}")
    print(f"{'OK' if not errs else 'FAILED'}: {len(errs)} problem(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
