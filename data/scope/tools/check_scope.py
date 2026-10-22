"""Data checks for data/scope (protocol evals/protocols/m5g-gate.md). Pure Python plus PyYAML; no network.

Checks: counts per label, split and style against the protocol minimums; even split per style;
duplicate text after normalisation; word-set Jaccard above 0.6 over scope rows and probes together;
no D22 phrase in an in-scope row (text or context); every follow_up row has recent_turns; no
demo-company or demo-owner name (read from data/demo, not written here); short and long length
bounds; lexicon structure (version, 25 fields with 2 to 5 synonyms, strong and weak disjoint).

Usage (from the repo root): uv run python data/scope/tools/check_scope.py
"""

from __future__ import annotations

import itertools
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage1_sim import SCOPE_DIR, load_lexicon, read_jsonl, term_strengths  # noqa: E402

DATA_DIR = SCOPE_DIR.parent
# SPEC D22 taint phrases.
D22 = [
    "system override",
    "system note",
    "ignore previous",
    "ignore all previous",
    "mark every field",
    "set the price to zero",
    "set the quote to zero",
]
MIN_IN = {
    "short": 40,
    "long": 30,
    "typo": 40,
    "fragment": 30,
    "multi_part": 30,
    "follow_up": 30,
    "greeting_business": 20,
    "session_action": 20,
}
MIN_OUT = {
    "chat": 24,
    "creative": 16,
    "code_homework": 16,
    "unrelated_advice": 16,
    "personal_to_assistant": 12,
    "rule_change": 16,
    "other_products": 12,
    "other": 8,
}


def norm_text(t: str) -> str:
    t = re.sub(r"[^\w\s']", " ", t.casefold().replace("’", "'"))
    return " ".join(t.split())


def words(t: str) -> set[str]:
    return set(norm_text(t).split())


def demo_names() -> list[str]:
    names: set[str] = set()
    for f in sorted((DATA_DIR / "demo" / "companies").glob("*.yaml")):
        c = yaml.safe_load(f.read_text(encoding="utf-8"))["company"]
        names.add(c["display_name"])
        names.add(c["owner"]["name"])
    for f in sorted((DATA_DIR / "demo" / "conversations").glob("*.expected.json")):
        for m in re.finditer(
            r'"field_id": "(company_name|owner_name)".{0,200}?"value": "([^"]+)"',
            f.read_text(encoding="utf-8"),
            re.S,
        ):
            names.add(m.group(2))
    out = set()
    for n in names:
        out.add(n.casefold())
        first = n.split()[0].casefold()
        out.add(first)  # first word of a company name, first name of an owner
        if len(n.split()) == 2:
            out.add(n.split()[1].casefold())  # owner surname
    return sorted(out)


def main() -> int:
    rows = read_jsonl(SCOPE_DIR / "scope_set.jsonl")
    probes = read_jsonl(SCOPE_DIR / "probes.jsonl")
    lex = load_lexicon()
    fails: list[str] = []

    # counts
    labels = Counter((r["label"], r["split"]) for r in rows)
    print(f"rows {len(rows)}; probes {len(probes)}")
    for (label, split), n in sorted(labels.items()):
        print(f"  {label:13s} {split:4s} {n}")
    styles = Counter((r["label"], r["style"], r["split"]) for r in rows)
    print("style counts (dev / test / total, protocol minimum):")
    for label, mins in (("in_scope", MIN_IN), ("out_of_scope", MIN_OUT)):
        seen = sorted({s for (lab, s, _) in styles if lab == label})
        for s in seen:
            d, t = styles[(label, s, "dev")], styles[(label, s, "test")]
            m = mins.get(s, 0)
            flag = ""
            if d + t < m:
                flag += " BELOW-MIN"
            if d != t:
                flag += " UNEVEN"
            print(f"  {label:13s} {s:22s} {d:3d} / {t:3d} / {d + t:3d}  min {m}{flag}")
            if flag:
                fails.append(f"style {label}/{s}:{flag}")
        for s in mins:
            if s not in seen:
                fails.append(f"style {label}/{s} missing")

    # ids and fields
    sids = [r["sid"] for r in rows] + [p["sid"] for p in probes]
    if len(sids) != len(set(sids)):
        fails.append("duplicate sid")
    for r in rows:
        if r["label"] not in ("in_scope", "out_of_scope") or r["split"] not in (
            "dev",
            "test",
        ):
            fails.append(f"{r['sid']} bad label or split")
        if "session_title" in r:
            fails.append(f"{r['sid']} carries session_title (D145)")

    # duplicates after normalisation
    items = [(r["sid"], r["text"]) for r in rows] + [
        (p["sid"], p["text"]) for p in probes if p["text"].strip()
    ]
    by_norm: dict[str, list[str]] = {}
    for sid, t in items:
        by_norm.setdefault(norm_text(t), []).append(sid)
    dups = {k: v for k, v in by_norm.items() if len(v) > 1}
    dup_bad = {k: v for k, v in dups.items() if any(s.startswith("s") for s in v)}
    dup_probe_only = {k: v for k, v in dups.items() if k not in dup_bad}
    print(f"duplicates after normalisation involving a scope row: {len(dup_bad)}")
    for k, v in dup_bad.items():
        fails.append(f"duplicate {v}")
    print(
        "  probe-only repeats (by design, with and without context): "
        + "; ".join(f"{'/'.join(v)}" for v in dup_probe_only.values())
    )

    # Jaccard
    wsets = [(sid, words(t)) for sid, t in items]
    high = []
    for (a, wa), (b, wb) in itertools.combinations(wsets, 2):
        if not wa or not wb:
            continue
        j = len(wa & wb) / len(wa | wb)
        if j > 0.6 and norm_text(dict(items)[a]) != norm_text(dict(items)[b]):
            high.append((a, b, round(j, 2)))
    print(
        f"pairs with word-set Jaccard > 0.6 (scope and probes, non-identical): {len(high)}"
    )
    for a, b, j in high:
        print(f"  {a} {b} {j}")
        if a.startswith("s") or b.startswith("s"):
            fails.append(f"jaccard {a} {b} {j}")

    # D22
    d22_hits = []
    for r in rows:
        if r["label"] != "in_scope":
            continue
        blob = " ".join([r["text"]] + [t["text"] for t in r["recent_turns"]]).casefold()
        d22_hits += [(r["sid"], p) for p in D22 if p in blob]
    print(f"D22 phrases in in-scope rows: {len(d22_hits)}")
    fails += [f"D22 {s} {p}" for s, p in d22_hits]

    # follow-ups, lengths, recent_turns shape
    no_ctx = [
        r["sid"] for r in rows if r["style"] == "follow_up" and not r["recent_turns"]
    ]
    print(f"follow_up rows without recent_turns: {len(no_ctx)}")
    fails += [f"follow_up without context {s}" for s in no_ctx]
    for r in rows:
        n = len(r["text"].split())
        if r["style"] == "short" and n >= 8:
            fails.append(f"{r['sid']} short row has {n} words")
        if r["style"] == "long" and not 40 <= n <= 120:
            fails.append(f"{r['sid']} long row has {n} words")
        if len(r["recent_turns"]) > 2 or any(
            t["role"] not in ("owner", "assistant") for t in r["recent_turns"]
        ):
            fails.append(f"{r['sid']} recent_turns shape")

    # demo names
    names = demo_names()
    name_hits = []
    for sid, blob in [
        (r["sid"], " ".join([r["text"]] + [t["text"] for t in r["recent_turns"]]))
        for r in rows
    ] + [
        (p["sid"], " ".join([p["text"]] + [t["text"] for t in p["recent_turns"]]))
        for p in probes
    ]:
        b = blob.casefold()
        name_hits += [
            sid for n in names if re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", b)
        ]
    print(
        f"demo-company or demo-owner names ({len(names)} forms checked): {len(name_hits)}"
    )
    fails += [f"demo name in {s}" for s in name_hits]

    # lexicon
    strengths = term_strengths(
        lex
    )  # raises if strong and weak overlap or a term is not lower-case
    fields = lex["protocol_fields"]
    bad_fields = [f["id"] for f in fields if not 2 <= len(f["synonyms"]) <= 5]
    print(
        f"lexicon {lex['version']} updated {lex['updated']}: {len(fields)} protocol fields, "
        f"{len(strengths)} rule-2 terms ({sum(v == 'strong' for v in strengths.values())} strong, "
        f"{sum(v == 'weak' for v in strengths.values())} weak), strong and weak disjoint"
    )
    if len(fields) != 25 or bad_fields:
        fails.append(f"protocol fields {len(fields)}, bad synonym counts {bad_fields}")
    for p in lex["refuse_patterns"]:
        re.compile(p["pattern"])

    print()
    if fails:
        print(f"FAIL ({len(fails)}):")
        for f in fails:
            print("  " + f)
        return 1
    print("ALL CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
