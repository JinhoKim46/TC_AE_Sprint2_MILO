"""The reply check (SPEC D108, D39, section 5 step 7). Code, not a model call.

Three rules, applied to every discovery reply before the owner sees it:

1. Money: the model never receives the estimate's figures, so any money amount in a reply is invented. Every
   sentence with a money amount is removed and replaced by one fixed sentence that points to the estimate panel
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
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
SCOPE_REPLY = "I can only help with the diagnosis of your business processes here."
PANEL_SENTENCE = "The estimate panel shows the current figures, calculated from your answers and our rate card."
NOT_YET_SENTENCE = "I can show an estimate once the assessment questions are answered."
BLOCKED_SENTENCE = "I cannot show an estimate until the conflicting answer is resolved."


@dataclass(frozen=True)
class ReplyCheck:
    text: str
    money_removed: bool = False
    out_of_scope: bool = False
    question_appended: bool = False
    verdicts: tuple[str, ...] = field(default=())


def has_money(text: str) -> bool:
    return bool(MONEY.search(text or ""))


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
) -> ReplyCheck:
    verdicts: list[str] = []
    body = (text or "").strip()
    money = has_money(body)
    if money:
        kept = [s for s in _SENTENCE.split(body) if not has_money(s)]
        if view.number_allowed:
            note = PANEL_SENTENCE
        elif view.estimate_status == "blocked":
            note = BLOCKED_SENTENCE
        else:
            note = NOT_YET_SENTENCE
        body = " ".join([*kept, note]).strip()
        verdicts.append("money_removed")
    in_scope = (
        "?" in body or _mentions(body, scope_terms) or _mentions(body, doc_ids) or money
    )
    out_of_scope = not in_scope
    if out_of_scope:
        body = SCOPE_REPLY
        verdicts.append("out_of_scope")
    appended = False
    if view.next_question and "?" not in body:
        body = f"{body} {view.next_question}".strip()
        appended = True
        verdicts.append("question_appended")
    return ReplyCheck(body, money, out_of_scope, appended, tuple(verdicts))
