"""Stage-1 simulation of the M5G input gate (protocol evals/protocols/m5g-gate.md, SPEC D110, D157).

Pure Python plus PyYAML; no model call, no network call, no .env. Reads `lexicon.yaml`,
`scope_set.jsonl` and `probes.jsonl` from data/scope and prints, per split and label, how many rows
land in each stage-1 outcome. This is a simulation for the data audit, not the gate itself
(the gate is src/milo/gate/stage1.py, D128).

Rules, in order; the first that fires decides (the amended rule table):
1. greeting: the whole text, after stripping punctuation and emoji, is on the greeting list.
2. pass: at least one strong term, or at least two distinct weak terms. Terms are matched case-folded
   with word boundaries, longest first, on non-overlapping spans, so "job sheet" is one term and
   "staff assignment" (strong) does not also yield "staff" (weak). A singular and its plural
   ("job", "jobs") count as one weak term.
3. pass: a digit sequence followed, within two words, by a number unit.
4. refuse: a verb-plus-object refuse pattern matches (only reached when rules 2 and 3 did not pass).
6. undecided: anything else, including a single weak term. Only `text` is read; `recent_turns`
   are stage-2 state.

Usage (from the repo root):
    uv run python data/scope/tools/stage1_sim.py            # summary tables
    uv run python data/scope/tools/stage1_sim.py --rows     # plus every out-of-scope pass and in-scope refusal
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

SCOPE_DIR = Path(__file__).resolve().parent.parent
OUTCOMES = ("greeting", "pass", "refuse", "undecided")
ENTRY_GROUPS = ("modules", "integrations", "kb_titles")
LIST_GROUPS = (
    "product_words",
    "trade_words",
    "generic_words",
    "session_actions",
    "account_actions",
)


def norm(text: str) -> str:
    return text.casefold().replace("’", "'").replace("‘", "'")


def load_lexicon(path: Path | None = None) -> dict:
    return yaml.safe_load(
        (path or SCOPE_DIR / "lexicon.yaml").read_text(encoding="utf-8")
    )


def term_strengths(lex: dict) -> dict[str, str]:
    """Map every rule-2 term to `strong` or `weak` (D157). Raises if a term is in both."""
    strength = lex["strength"]
    strong: set[str] = set()
    weak: set[str] = set()

    def add(terms, group_strength):
        (strong if group_strength == "strong" else weak).update(terms)

    for group in ENTRY_GROUPS:
        for entry in lex[group]:
            heads = [entry[k] for k in ("id", "name", "title") if k in entry]
            # ids such as "phone-intake" are also matched as "phone intake"
            heads += [h.replace("-", " ") for h in heads if "-" in h]
            add(heads + list(entry.get("short_forms", [])), strength[group])
            add(entry.get("weak_forms", []), "weak")
    for field in lex["protocol_fields"]:
        add(
            [field["id"], field["id"].replace("_", " ")] + list(field["synonyms"]),
            strength["protocol_fields"],
        )
    for group in LIST_GROUPS:
        add(lex[group], strength[group])

    both = strong & weak
    if both:
        raise ValueError(f"terms marked both strong and weak: {sorted(both)}")
    upper = [t for t in strong | weak if t != t.lower()]
    if upper:
        raise ValueError(f"terms not lower-case: {upper}")
    out = {t: "strong" for t in strong}
    out.update({t: "weak" for t in weak})
    return out


def _bounded(term: str) -> str:
    return r"(?<!\w)" + re.escape(term) + r"(?!\w)"


def weak_key(term: str) -> str:
    """Singular and plural of one weak term count once ("job" / "jobs", "enquiry" / "enquiries")."""
    if term.endswith("ies"):
        return term[:-3] + "y"
    if term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


class Stage1:
    def __init__(self, lex: dict):
        self.lex = lex
        self.strength = term_strengths(lex)
        # longest first, so a multi-word term claims its span before any word inside it
        self.terms = sorted(self.strength, key=lambda t: (-len(t), t))
        self.term_res = [(t, re.compile(_bounded(t))) for t in self.terms]
        self.greetings = {self._greeting_norm(g) for g in lex["greetings"]}
        units = sorted(lex["number_units"], key=len, reverse=True)
        unit_alt = "|".join(re.escape(u) for u in units)
        # a number, then at most two intervening words, then a unit; "/day" and "%" may attach
        self.number_re = re.compile(
            r"(?<![\w])\d+(?:[.,]\d+)*\s*(?:[^\W\d][\w'-]*\s+){0,2}?(?:"
            + unit_alt
            + r")(?!\w)"
        )
        self.refuse = [
            (p["id"], re.compile(p["pattern"])) for p in lex["refuse_patterns"]
        ]

    @staticmethod
    def _greeting_norm(text: str) -> str:
        text = re.sub(r"[^\w\s']", " ", norm(text))
        return " ".join(text.split())

    def matched_terms(self, text: str) -> list[tuple[str, str]]:
        t = norm(text)
        taken = [False] * len(t)
        found = []
        for term, rx in self.term_res:
            for m in rx.finditer(t):
                if any(taken[m.start() : m.end()]):
                    continue
                for i in range(m.start(), m.end()):
                    taken[i] = True
                found.append((term, self.strength[term]))
        return found

    def classify(self, text: str) -> tuple[str, str]:
        """Return (outcome, reason)."""
        if self._greeting_norm(text) in self.greetings:
            return "greeting", "rule 1"
        found = self.matched_terms(text)
        strong = sorted({t for t, s in found if s == "strong"})
        weak = sorted({weak_key(t) for t, s in found if s == "weak"})
        if strong:
            return "pass", "rule 2 strong: " + ", ".join(strong)
        if len(weak) >= 2:
            return "pass", "rule 2 two weak: " + ", ".join(weak)
        m = self.number_re.search(norm(text))
        if m:
            return "pass", "rule 3: " + m.group(0)
        for pid, rx in self.refuse:
            if rx.search(norm(text)):
                return "refuse", "rule 4: " + pid
        if weak:
            return "undecided", "one weak: " + weak[0]
        return "undecided", "no term"


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--rows",
        action="store_true",
        help="list out-of-scope passes and in-scope refusals",
    )
    args = ap.parse_args(argv)

    lex = load_lexicon()
    gate = Stage1(lex)
    rows = read_jsonl(SCOPE_DIR / "scope_set.jsonl")
    probes = read_jsonl(SCOPE_DIR / "probes.jsonl")

    table: dict[tuple[str, str], Counter] = {}
    oos_pass, in_refuse = [], []
    for r in rows:
        outcome, reason = gate.classify(r["text"])
        table.setdefault((r["split"], r["label"]), Counter())[outcome] += 1
        if r["label"] == "out_of_scope" and outcome == "pass":
            oos_pass.append((r["sid"], r["split"], r["style"], reason, r["text"]))
        if r["label"] == "in_scope" and outcome == "refuse":
            in_refuse.append((r["sid"], r["split"], reason, r["text"]))

    print(
        f"lexicon {lex['version']} ({lex['updated']}); {len(gate.strength)} rule-2 terms, "
        f"{sum(s == 'strong' for s in gate.strength.values())} strong, "
        f"{sum(s == 'weak' for s in gate.strength.values())} weak"
    )
    print()
    print("| Split | Label | n | greeting | pass | refuse | undecided |")
    print("|---|---|---|---|---|---|---|")
    for split in ("dev", "test"):
        for label in ("in_scope", "out_of_scope"):
            c = table.get((split, label), Counter())
            n = sum(c.values())
            print(
                f"| {split} | {label} | {n} | "
                + " | ".join(str(c[o]) for o in OUTCOMES)
                + " |"
            )

    probe_table: dict[str, Counter] = {}
    for p in probes:
        if p["category"] == "empty":
            continue
        outcome, _ = gate.classify(p["text"])
        probe_table.setdefault(p["expected"], Counter())[outcome] += 1
    print()
    print(
        "| Probes by expected (empty rows excluded) | greeting | pass | refuse | undecided |"
    )
    print("|---|---|---|---|---|")
    for exp in ("pass", "refuse", "band"):
        c = probe_table.get(exp, Counter())
        print(f"| {exp} | " + " | ".join(str(c[o]) for o in OUTCOMES) + " |")

    if args.rows:
        print()
        print("Out-of-scope rows that pass stage 1:")
        for sid, split, style, reason, text in oos_pass:
            print(f"  {sid} {split} {style}: {reason} | {text}")
        print("In-scope rows refused by stage 1:")
        for sid, split, reason, text in in_refuse or [("-", "-", "none", "")]:
            print(f"  {sid} {split}: {reason} | {text}")

    dev_in = table[("dev", "in_scope")]["undecided"]
    dev_out = table[("dev", "out_of_scope")]["undecided"]
    test_out_pass = table[("test", "out_of_scope")]["pass"]
    checks = [
        ("in-scope rows refused (all splits) == 0", len(in_refuse) == 0),
        (
            f"out-of-scope test rows passing stage 1 <= 3 (got {test_out_pass})",
            test_out_pass <= 3,
        ),
        (f"undecided dev in-scope >= 40 (got {dev_in})", dev_in >= 40),
        (f"undecided dev out-of-scope >= 40 (got {dev_out})", dev_out >= 40),
    ]
    print()
    for name, ok in checks:
        print(("OK   " if ok else "FAIL ") + name)
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
