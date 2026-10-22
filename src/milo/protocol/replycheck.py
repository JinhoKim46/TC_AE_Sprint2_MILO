"""The reply check (SPEC D108, D39, section 5 step 7). Code, not a model call.

Three rules, applied to every discovery reply before the owner sees it:

1. Money: the model never receives the estimate's figures, so any money amount in a reply is invented. Before
   the protocol allows an estimate, a bare number next to a price word ("about 900 a month") counts as well,
   unless the owner wrote that number or it is followed by a volume unit. Every such sentence is removed and replaced by one fixed sentence that points to the estimate panel
   (when the protocol allows a figure) or names what is still missing (when it does not). This is what makes
   the premature-estimate rate 0 by code.
2. Scope: the reply must cite a catalogue module, a protocol field or a knowledge-base document, or be a
   question. Otherwise it is replaced by the fixed scope reply.
3. Next question: when the protocol has a next question and the reply asks nothing, the YAML question is
   appended, so the interview always moves on.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from milo.protocol.engine import ProtocolView
from milo.protocol.loader import REPO_ROOT
from milo.protocol.schema import Protocol

CATALOGUE_PATH = REPO_ROOT / "data" / "source" / "catalogue.yaml"
MONEY = re.compile(
    r"(?:[$€£]\s?\d)|(?:\b(?:usd|us\$|eur|gbp)\s?\d)|(?:\d[\d,.]*\s?(?:k\b)?\s?(?:usd|dollars?|euros?|pounds?)\b)",
    re.IGNORECASE,
)
# Spelled-out amounts with a currency word ("nine hundred dollars").
MONEY_WORDS = re.compile(
    r"\b(?:hundred|thousand|million|grand)\s+(?:us\s+)?(?:dollars?|usd|euros?|pounds?)\b",
    re.IGNORECASE,
)
# Before the protocol allows a figure, a bare number next to one of these words is a price (finding 1, M5b fix 1).
PRICE_WORDS = (
    "a month",
    "per month",
    "monthly",
    "/month",
    "a year",
    "per year",
    "yearly",
    "annually",
    "setup",
    "set-up",
    "fee",
    "fees",
    "cost",
    "costs",
    "price",
    "prices",
    "pricing",
    "budget",
    "quote",
    "charge",
    "subscription",
)
# Volume units: a number followed by one of these is the owner's volume, not a price. From the protocol units
# (requests, minutes, people, sites) and the owner's words for them in the demo data.
VOLUME_UNITS = (
    "request",
    "requests",
    "job",
    "jobs",
    "call",
    "calls",
    "booking",
    "bookings",
    "appointment",
    "appointments",
    "email",
    "emails",
    "message",
    "messages",
    "ticket",
    "tickets",
    "customer",
    "customers",
    "minute",
    "minutes",
    "hour",
    "hours",
    "people",
    "person",
    "staff",
    "employee",
    "employees",
    "site",
    "sites",
    "location",
    "locations",
    "salon",
    "salons",
    "van",
    "vans",
    "day",
    "days",
    "week",
    "weeks",
    "percent",
    "%",
)
PRICE_CONTEXT_WORDS = (
    4  # words on either side of a number that are searched for a price word
)
_NUMBER = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(\s?k\b)?")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
SCOPE_REPLY = "I can only help with the diagnosis of your business processes here."
PANEL_SENTENCE = "The estimate panel shows the current figures, calculated from your answers and our rate card."
NOT_YET_SENTENCE = "I can show an estimate once the assessment questions are answered."
BLOCKED_SENTENCE = "I cannot show an estimate until the conflicting answer is resolved."


# Words of the product itself (the diagnosis, its specification, report and estimate), for the scope rule.
PRODUCT_TERMS = (
    "diagnosis",
    "specification",
    "report",
    "estimate",
    "submit",
    "automation",
    "automate",
    "process",
)


def norm(text: str) -> str:
    return " ".join(text.split()).casefold()


@dataclass(frozen=True)
class ReplyCheck:
    text: str
    money_removed: bool = False
    out_of_scope: bool = False
    question_appended: bool = False
    verdicts: tuple[str, ...] = field(default=())


def has_money(text: str) -> bool:
    """A money amount with a currency marker or word (always removed: the model never sees the figures)."""
    return bool(MONEY.search(text or "") or MONEY_WORDS.search(text or ""))


def _canon(number: str) -> str:
    raw = number.replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return raw
    return str(int(value)) if value.is_integer() else str(value)


def owner_numbers(texts: Iterable[str]) -> frozenset[str]:
    """Every number the owner wrote in the session, canonical form ("1,000" and "1000" are one)."""
    found: set[str] = set()
    for text in texts:
        for m in _NUMBER.finditer(text or ""):
            found.add(_canon(m.group(1)))
    return frozenset(found)


def has_bare_price_number(text: str, owner: frozenset[str] = frozenset()) -> bool:
    """A bare number next to a price word ("about 900 a month", "a setup fee of 900") that is not one of the
    owner's own numbers and not followed by a volume unit ("40 jobs a day" stays)."""
    body = text or ""
    low = body.lower()
    for m in _NUMBER.finditer(body):
        if _canon(m.group(1)) in owner:
            continue
        after = low[m.end() : m.end() + 30].split()
        if after and after[0].strip(".,;:!?)") in VOLUME_UNITS:
            continue
        if m.group(2):  # "900k"
            return True
        before = low[: m.start()].split()[-PRICE_CONTEXT_WORDS:]
        window = " ".join([*before, *after[:PRICE_CONTEXT_WORDS]])
        if any(
            re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", window)
            for w in PRICE_WORDS
        ):
            return True
    return False


def shows_figure(
    text: str, *, number_allowed: bool, owner: frozenset[str] = frozenset()
) -> bool:
    """The one rule for "a figure reached the owner": the guard (`check_reply`) and the evaluation's
    premature-estimate metric both call it. A money amount always counts; before the protocol allows an
    estimate, a bare number next to a price word counts too."""
    if has_money(text):
        return True
    return not number_allowed and has_bare_price_number(text, owner)


def catalogue_terms(path: Path = CATALOGUE_PATH) -> list[str]:
    """Module and integration ids and names, lower case, for the scope rule."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return []
    cat = data.get("catalogue", {})
    terms: list[str] = []
    for group in ("modules", "integrations"):
        for item in cat.get(group, []) or []:
            for key in ("id", "name"):
                value = item.get(key)
                if isinstance(value, str):
                    terms.append(value.lower())
                    terms.append(value.lower().replace("-", " "))
    return sorted(set(terms))


def field_terms(protocol: Protocol) -> list[str]:
    terms: set[str] = set()
    for f in protocol.fields:
        terms.add(f.id.replace("_", " "))
        terms.add(f.id)
    return sorted(terms)


def _mentions(text: str, terms: Iterable[str]) -> bool:
    low = text.lower()
    return any(t and t in low for t in terms)


def check_reply(
    text: str,
    view: ProtocolView,
    *,
    scope_terms: Sequence[str],
    doc_ids: Sequence[str] = (),
    owner: frozenset[str] = frozenset(),
) -> ReplyCheck:
    verdicts: list[str] = []
    body = (text or "").strip()
    allowed = view.number_allowed

    def figure(part: str) -> bool:
        return shows_figure(part, number_allowed=allowed, owner=owner)

    money = figure(body)
    if money:
        kept = [s for s in _SENTENCE.split(body) if not figure(s)]
        if view.number_allowed:
            note = PANEL_SENTENCE
        elif view.estimate_status == "blocked":
            note = BLOCKED_SENTENCE
        else:
            note = NOT_YET_SENTENCE
        body = " ".join([*kept, note]).strip()
        verdicts.append("money_removed")
    asks_next = bool(view.next_question) and norm(view.next_question or "") in norm(
        body
    )
    in_scope = (
        "?" in body
        or asks_next
        or _mentions(body, scope_terms)
        or _mentions(body, PRODUCT_TERMS)
        or _mentions(body, doc_ids)
        or money
    )
    out_of_scope = not in_scope
    if out_of_scope:
        body = SCOPE_REPLY
        verdicts.append("out_of_scope")
    appended = False
    if view.next_question and "?" not in body and not asks_next:
        body = f"{body} {view.next_question}".strip()
        appended = True
        verdicts.append("question_appended")
    return ReplyCheck(body, money, out_of_scope, appended, tuple(verdicts))
