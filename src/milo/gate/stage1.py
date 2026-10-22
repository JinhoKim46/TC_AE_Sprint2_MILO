"""Stage 1 of the input gate: deterministic, free, no model and no network (SPEC D103, D110, D157, D158).

Rules, in order; the first that fires decides (protocol `evals/protocols/m5g-gate.md`, amended rule table):

1. `greeting`: the whole text, after stripping punctuation and emoji, is on the lexicon's greeting list.
2. `pass`: at least one strong lexicon term, or at least two distinct weak terms. Terms are matched case-folded with
   word boundaries, longest first, on non-overlapping spans; a singular and its plural count as one weak term (D158).
3. `pass`: a number followed, within two words, by a lexicon unit; the number may be approximate ("40ish", "~40",
   "about 40", "40-50", "10k") (D158).
4. `refuse`: a verb-plus-object refuse pattern matches and the text holds no lexicon term of either strength (the
   protocol's "and no lexicon term"); a mixed message with one weak term goes to stage 2, never to a refusal.
6. `undecided`: anything else, including a single weak term; stage 2 decides.

This is the reference simulation `data/scope/tools/stage1_sim.py` made into product code; `tests/test_gate.py`
checks that both give the same outcome and reason on every dev scope row and every probe.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

ROOT = Path(__file__).resolve().parents[3]
LEXICON_PATH = ROOT / "data" / "scope" / "lexicon.yaml"

Stage1Outcome = Literal["greeting", "pass", "refuse", "undecided"]
OUTCOMES: tuple[Stage1Outcome, ...] = ("greeting", "pass", "refuse", "undecided")
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


def load_lexicon(path: Path | None = None) -> dict[str, Any]:
    data = yaml.safe_load((path or LEXICON_PATH).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("lexicon.yaml must be a mapping")
    return data


def term_strengths(lex: dict[str, Any]) -> dict[str, str]:
    """Map every rule-2 term to `strong` or `weak` (D157). Raises if a term is in both or not lower-case."""
    strength = lex["strength"]
    strong: set[str] = set()
    weak: set[str] = set()

    def add(terms: list[str], group_strength: str) -> None:
        (strong if group_strength == "strong" else weak).update(terms)

    for group in ENTRY_GROUPS:
        for entry in lex[group]:
            heads = [entry[k] for k in ("id", "name", "title") if k in entry]
            heads += [h.replace("-", " ") for h in heads if "-" in h]
            add(heads + list(entry.get("short_forms", [])), strength[group])
            add(list(entry.get("weak_forms", [])), "weak")
    for fld in lex["protocol_fields"]:
        add(
            [fld["id"], fld["id"].replace("_", " ")]
            + list(fld["synonyms"])
            + list(fld.get("short_forms", [])),
            strength["protocol_fields"],
        )
    for group in LIST_GROUPS:
        add(list(lex[group]), strength[group])

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
    """Singular and plural of one weak term count once ("job" / "jobs", "enquiry" / "enquiries"), D158."""
    if term.endswith("ies"):
        return term[:-3] + "y"
    if term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


class Stage1:
    def __init__(self, lex: dict[str, Any]) -> None:
        self.lex = lex
        self.version = str(lex.get("version", ""))
        self.strength = term_strengths(lex)
        # longest first, so a multi-word term claims its span before any word inside it
        self.terms = sorted(self.strength, key=lambda t: (-len(t), t))
        self.term_res = [(t, re.compile(_bounded(t))) for t in self.terms]
        self.greetings = {self._greeting_norm(g) for g in lex["greetings"]}
        units = sorted(lex["number_units"], key=len, reverse=True)
        unit_alt = "|".join(re.escape(u) for u in units)
        # a number, then at most two intervening words, then a unit; "/day" and "%" may attach
        self.number_re = re.compile(
            r"(?<![\w])~?\d+(?:[.,]\d+)*(?:\s*[-–]\s*\d+(?:[.,]\d+)*)?"
            r"(?:ish|k|x)?\s*(?:[^\W\d][\w'-]*\s+){0,2}?(?:" + unit_alt + r")(?!\w)"
        )
        self.refuse = [
            (str(p["id"]), re.compile(p["pattern"])) for p in lex["refuse_patterns"]
        ]

    @staticmethod
    def _greeting_norm(text: str) -> str:
        text = re.sub(r"[^\w\s']", " ", norm(text))
        return " ".join(text.split())

    def matched_terms(self, text: str) -> list[tuple[str, str]]:
        t = norm(text)
        taken = [False] * len(t)
        found: list[tuple[str, str]] = []
        for term, rx in self.term_res:
            for m in rx.finditer(t):
                if any(taken[m.start() : m.end()]):
                    continue
                for i in range(m.start(), m.end()):
                    taken[i] = True
                found.append((term, self.strength[term]))
        return found

    def classify(self, text: str) -> tuple[Stage1Outcome, str]:
        """Return (outcome, reason). Reads the message text only; `recent_turns` are stage-2 state."""
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
        if (
            not found
        ):  # rule 4 only when the text holds no lexicon term of either strength
            for pid, rx in self.refuse:
                if rx.search(norm(text)):
                    return "refuse", "rule 4: " + pid
        if weak:
            return "undecided", "one weak: " + weak[0]
        return "undecided", "no term"


@lru_cache(maxsize=1)
def default_stage1() -> Stage1:
    """The stage-1 gate over the committed lexicon (loaded once per process)."""
    return Stage1(load_lexicon())
