"""E02 chat-model comparison (protocol `evals/protocols/e02-chat-compare.md`; SPEC D3, D24, D56, D148).

Modes:
    python evals/chat/compare_chat.py --replay [--result PATH]   recompute every score and metric from `rows`
    MILO_LIVE=1 python evals/chat/compare_chat.py --live          the paid run (integrator only, HARNESS rule 14)

Replay makes no network call and reads no key. The live path refuses to start without MILO_LIVE=1, verifies each
candidate slug against the OpenRouter catalogue before any paid call, and checks the planned spend against the
reserve (D18) and the protocol cap. Models are reached only through `milo.llm.cloud` (HARNESS rule 1).
Gold is loaded through `evals/common.py` only; test conversations need `MILO_FINAL=1` (D149).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from evals import common  # noqa: E402
from milo.errors import ProviderFailure  # noqa: E402
from milo.llm.client import cost_usd, invoke_json  # noqa: E402
from milo.llm.tokens import count_message_tokens  # noqa: E402
from milo.types import ChatReply  # noqa: E402

CHAT_DIR = ROOT / "evals" / "chat"
RESULT_DIR = ROOT / "evals" / "results" / "e02"
FIXTURE = CHAT_DIR / "fixtures" / "chat_dev_fixture.json"
DEFAULT_RESULT = RESULT_DIR / "chat_dev.json"
EXTRACT_PROMPT = CHAT_DIR / "prompts" / "extract.v1.md"
REPLY_PROMPT = CHAT_DIR / "prompts" / "reply.v1.md"
PROTOCOL_YAML = ROOT / "protocols" / "discovery.v1.yaml"
PROTOCOL_DOC = ROOT / "evals" / "protocols" / "e02-chat-compare.md"

REFERENCE_NAME = "gpt-4.1-mini"
# Proposed slugs. Each one is verified against the catalogue before any paid call (protocol, Data/Models).
CANDIDATE_SLUGS = {
    "deepseek-v4-flash": "deepseek/deepseek-v4-flash",
    "gpt-5-nano": "openai/gpt-5-nano",
    "gemini-2.5-flash-lite": "google/gemini-2.5-flash-lite",
    "gpt-4.1-nano": "openai/gpt-4.1-nano",
    "claude-haiku-5.5": "anthropic/claude-haiku-5.5",
}
REFERENCE_SLUG = "openai/gpt-4.1-mini"
DEV_CONVERSATIONS = ("A-1", "B-1", "E-1")
TEST_CONVERSATIONS = ("A-2", "B-2", "E-2")
EMBEDDING_MODEL = "google/gemini-embedding-2"
CHUNK_SIZE, CHUNK_OVERLAP, TOP_K = 800, 100, 5
ABSTAIN = "INSUFFICIENT_CONTEXT"
BOOTSTRAP, SEED = 2000, 0
CAP_USD = 1.20
REASON = "candidate chat model output needed for the comparison"
LIVE_REFUSAL = (
    "refusing to run: `make eval-chat` spends OpenRouter budget (SPEC D126) and needs MILO_LIVE=1. "
    "Use `make eval-chat-replay` to recompute from a recorded result."
)

# ---------------------------------------------------------------- normalisation (task A)

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50,
}  # fmt: skip
_BUSINESS_DAYS = {"month": 21, "week": 5, "day": 1}


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _fold(v: Any) -> str:
    return _collapse(str(v)).casefold()


def _as_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        t = v.strip().casefold().replace(",", "")
        if t in _NUMBER_WORDS:
            return float(_NUMBER_WORDS[t])
        try:
            return float(t)
        except ValueError:
            return None
    return None


def canonical_value(value: Any, period: str | None) -> Any:
    """A per-month or per-week count becomes per business day (protocol `conversions`)."""
    num = _as_number(value)
    days = _BUSINESS_DAYS.get(str(period or "").casefold())
    if num is not None and days and days != 1:
        return num / days
    return value


def values_match(expected: Any, predicted: Any) -> bool:
    if isinstance(expected, list):
        if not isinstance(predicted, list):
            predicted = [predicted]
        return {_fold(x) for x in expected} == {_fold(x) for x in predicted}
    e, p = _as_number(expected), _as_number(predicted)
    if e is not None and p is not None:
        return abs(e - p) <= 0.01 * max(abs(e), 1e-9)
    return _fold(expected) == _fold(predicted)


def score_turn(
    expected: list[dict[str, Any]],
    earlier_fields: list[str],
    predicted: list[dict[str, Any]] | None,
    owner_text: str,
) -> dict[str, Any]:
    """Counts for one turn (protocol, expected-value normalisation).

    A predicted fact for a field expected this turn is a hit when the value matches, otherwise a false positive.
    A fact for a field expected only in an earlier turn is neutral. Any other field is a false positive.
    """
    exp = {f["field_id"]: f["value"] for f in expected}
    tp = fp = 0
    verbatim = total = 0
    hit: set[str] = set()
    text = _collapse(owner_text)
    for fact in predicted or []:
        fid = str(fact.get("field_id", ""))
        total += 1
        ev = _collapse(str(fact.get("evidence_text", "")))
        verbatim += int(bool(ev) and ev in text)
        if fid in exp:
            value = canonical_value(fact.get("value"), fact.get("period"))
            if fid not in hit and values_match(exp[fid], value):
                tp += 1
                hit.add(fid)
            else:
                fp += 1
        elif fid not in earlier_fields:
            fp += 1
    fn = len(exp) - len(hit)
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "precision": prec, "recall": rec, "f1": f1,
        "facts": total, "verbatim": verbatim,
    }  # fmt: skip


def parse_facts(parsed: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    if parsed is None or not isinstance(parsed.get("facts"), list):
        return None
    return [f for f in parsed["facts"] if isinstance(f, dict)]


# ---------------------------------------------------------------- scoring (task B)


def score_reply(row: dict[str, Any]) -> dict[str, Any]:
    """Citation and abstention checks for one gold row (protocol, Metrics)."""
    raw = str(row.get("reply", ""))
    abstained = raw.strip() == ABSTAIN
    cited = re.findall(r"\[([^\[\]]+)\]", raw)
    context = set(row["context_ids"])
    gold = set(row["gold_chunk_ids"])
    out: dict[str, Any] = {"abstained": abstained, "cited": cited}
    if row["answerable"]:
        out["citation_ok"] = bool(set(cited) & gold) and all(
            c in context for c in cited
        )
        out["abstain_ok"] = (not abstained) if gold else None
    else:
        out["citation_ok"] = None
        out["abstain_ok"] = abstained
    return out


# ---------------------------------------------------------------- statistics


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(len(s) - 1, math.ceil(q * len(s)) - 1)]


def bootstrap_ci(xs: list[float]) -> tuple[float, float]:
    """95 percent percentile interval of the mean, 2000 resamples, seed 0 (D24)."""
    if not xs:
        return (float("nan"), float("nan"))
    rng = random.Random(SEED)
    n = len(xs)
    means = sorted(
        mean([xs[rng.randrange(n)] for _ in range(n)]) for _ in range(BOOTSTRAP)
    )
    return means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP) - 1]


def paired_diff_ci(a: dict[str, float], b: dict[str, float]) -> tuple[float, float]:
    keys = sorted(set(a) & set(b))
    return bootstrap_ci([a[k] - b[k] for k in keys])


def _r(x: float | None) -> float | None:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return round(x, 6)


# ---------------------------------------------------------------- metrics and decision


def rescore(row: dict[str, Any]) -> dict[str, Any]:
    """Recompute a row's `scores` from the raw reply and the stored expectations."""
    if row["task"] == "A":
        preds = parse_facts(row.get("parsed"))
        return score_turn(
            row["expected"], row["earlier_fields"], preds, row["owner_text"]
        )
    return score_reply(row)


def _key(row: dict[str, Any]) -> str:
    return f"{row['conv']}#{row['turn']}" if row["task"] == "A" else str(row["qid"])


def compute_metrics(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    models = sorted({r["model"] for r in rows})
    per: dict[str, dict[str, Any]] = {}
    for m in models:
        a = [r for r in rows if r["model"] == m and r["task"] == "A"]
        b = [r for r in rows if r["model"] == m and r["task"] == "B"]
        sa = [rescore(r) for r in a]
        sb = [rescore(r) for r in b]
        facts = sum(s["facts"] for s in sa)
        cit = [float(s["citation_ok"]) for s in sb if s["citation_ok"] is not None]
        abst = [float(s["abstain_ok"]) for s in sb if s["abstain_ok"] is not None]
        lat = [r["latency_s"] for r in a + b]
        grades = [r["grade"] for r in b if r.get("grade") in ("pass", "fail")]
        per[m] = {
            "n_a": len(a), "n_b": len(b),
            "f1": mean([s["f1"] for s in sa]),
            "precision": mean([s["precision"] for s in sa]),
            "recall": mean([s["recall"] for s in sa]),
            "json_valid_first": mean([float(r["parsed"] is not None and not r["retry"]) for r in a]),
            "json_valid_after_retry": mean([float(r["parsed"] is not None) for r in a]),
            "evidence_verbatim": (sum(s["verbatim"] for s in sa) / facts) if facts else float("nan"),
            "citation_correct": mean(cit),
            "abstention_correct": mean(abst),
            "answer_pass_rate": mean([float(g == "pass") for g in grades]),
            "cost_a": mean([r["cost_usd"] for r in a]),
            "cost_b": mean([r["cost_usd"] for r in b]),
            "latency_mean": mean(lat),
            "latency_p95": percentile(lat, 0.95),
            "provider_errors": sum(1 for r in a + b if r.get("error")),
            "f1_ci": bootstrap_ci([s["f1"] for s in sa]),
            "citation_ci": bootstrap_ci(cit),
            "abstention_ci": bootstrap_ci(abst),
        }  # fmt: skip
    return per


def f1_by_key(rows: list[dict[str, Any]], model: str) -> dict[str, float]:
    return {
        _key(r): rescore(r)["f1"]
        for r in rows
        if r["model"] == model and r["task"] == "A"
    }


def decide(
    rows: list[dict[str, Any]], metrics: dict[str, dict[str, Any]], reference: str
) -> dict[str, Any]:
    """The protocol's decision rule. Thresholds are fixed in the protocol, not here by choice."""
    ref = metrics[reference]
    ref_f1 = f1_by_key(rows, reference)
    verdicts: dict[str, Any] = {}
    for m, x in metrics.items():
        if m == reference:
            continue
        lo, hi = paired_diff_ci(f1_by_key(rows, m), ref_f1)
        checks = {
            "f1_within_0.05": x["f1"] >= ref["f1"] - 0.05,
            "paired_ci_not_below_-0.10": hi >= -0.10,
            "json_after_retry_0.98": x["json_valid_after_retry"] >= 0.98,
            "citation_within_0.05": x["citation_correct"]
            >= ref["citation_correct"] - 0.05,
            "abstention_within_0.05": x["abstention_correct"]
            >= ref["abstention_correct"] - 0.05,
            "no_persistent_provider_error": x["provider_errors"] == 0,
        }
        verdicts[m] = {
            "pass": all(checks.values()),
            "checks": checks,
            "paired_f1_ci": [_r(lo), _r(hi)],
        }
    passing = [m for m, v in verdicts.items() if v["pass"]]
    chosen = (
        min(passing, key=lambda m: (metrics[m]["cost_a"], m)) if passing else reference
    )
    line = (
        f"development model: {chosen} (cheapest passing candidate by task A cost)"
        if passing
        else f"development model: {reference} (no candidate passed; the reference is used)"
    )
    return {
        "reference": reference,
        "candidates": verdicts,
        "chosen": chosen,
        "line": line,
    }


def normalise_metrics(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m, x in metrics.items():
        out[m] = {
            k: (
                [_r(v) for v in val]
                if isinstance(val, tuple)
                else _r(val)
                if isinstance(val, float)
                else val
            )
            for k, val in x.items()
        }
    return out


# ---------------------------------------------------------------- catalogue check


def verify_slugs(
    catalogue: dict[str, dict[str, Any]],
    candidates: dict[str, str] | None = None,
    reference: tuple[str, str] = (REFERENCE_NAME, REFERENCE_SLUG),
) -> dict[str, Any]:
    """Pin each slug that the catalogue lists with a usable price; record the rest as dropped (protocol, Data).

    Returns `{"pinned": {slug: {"name", "prompt", "completion", "context"}}, "dropped": [{"name", "slug", "reason"}],
    "reference": slug}`. Raises when the reference is not usable: nothing can be compared without it.
    """
    wanted = {
        **(candidates if candidates is not None else CANDIDATE_SLUGS),
        reference[0]: reference[1],
    }
    pinned: dict[str, dict[str, Any]] = {}
    dropped: list[dict[str, str]] = []
    for name, slug in wanted.items():
        entry = catalogue.get(slug)
        if entry is None:
            dropped.append(
                {"name": name, "slug": slug, "reason": "not listed in the catalogue"}
            )
            continue
        if not all(
            isinstance(entry.get(k), float) and entry[k] == entry[k]
            for k in ("prompt", "completion")
        ):
            dropped.append({"name": name, "slug": slug, "reason": "price missing"})
            continue
        pinned[slug] = {"name": name, **entry}
    if reference[1] not in pinned:
        raise RuntimeError(
            f"reference {reference[1]} is not usable in the catalogue; E02 cannot run"
        )
    return {"pinned": pinned, "dropped": dropped, "reference": reference[1]}


# ---------------------------------------------------------------- data loading


def load_conversations(split: str) -> dict[str, dict[str, Any]]:
    """Demo conversations with expected facts. The test three need MILO_FINAL=1 (SPEC D149)."""
    if split == "test" and not common.final_mode():
        raise PermissionError(
            "test conversations are read only by MILO_FINAL=1 runs (D149)"
        )
    ids = DEV_CONVERSATIONS if split == "dev" else TEST_CONVERSATIONS
    base = ROOT / "data" / "demo" / "conversations"
    out = {}
    for cid in ids:
        turns = [
            json.loads(x)
            for x in (base / f"{cid}.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()
        ]
        exp = json.loads((base / f"{cid}.expected.json").read_text(encoding="utf-8"))
        out[cid] = {
            "turns": turns,
            "expected": {t["turn"]: t["facts"] for t in exp["turns"]},
        }
    return out


def field_list() -> str:
    import yaml

    proto = yaml.safe_load(PROTOCOL_YAML.read_text(encoding="utf-8"))
    return "\n".join(f"  - {f['id']} ({f['type']})" for f in proto["fields"])


def kb_chunks() -> list[dict[str, str]]:
    """800/100 character chunks per markdown section, as in `retrieval/compare_embeddings.py` (D9)."""
    out: list[tuple[str, str]] = []
    for md in sorted((ROOT / "data" / "kb").rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        doc_id = md.relative_to(ROOT / "data" / "kb").with_suffix("").as_posix()
        body = text.split("---", 2)[2] if text.startswith("---") else text
        sections: list[str] = []
        cur: list[str] = []
        for line in body.splitlines():
            if line.startswith("#") and cur:
                sections.append("\n".join(cur))
                cur = []
            cur.append(line)
        if cur:
            sections.append("\n".join(cur))
        for sec in sections:
            sec = sec.strip()
            start = 0
            while start < len(sec):
                out.append((doc_id, sec[start : start + CHUNK_SIZE]))
                if start + CHUNK_SIZE >= len(sec):
                    break
                start += CHUNK_SIZE - CHUNK_OVERLAP
    return [
        {"chunk_id": f"{d}#{i}", "doc_id": d, "text": t} for i, (d, t) in enumerate(out)
    ]


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return (
        sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0
    )


# ---------------------------------------------------------------- live run


class Metered:
    """Wraps a chat model to total tokens, cost and calls of one row (a JSON re-ask makes a second call)."""

    def __init__(self, chat: Any) -> None:
        self.chat = chat
        self.price_usd_per_mtok = chat.price_usd_per_mtok
        self.tokens_in = self.tokens_out = self.calls = 0
        self.cost = 0.0

    def invoke(self, messages: list[dict[str, Any]], tools: Any = None) -> ChatReply:
        reply = self.chat.invoke(messages, tools)
        self.calls += 1
        self.tokens_in += reply.usage.input_tokens
        self.tokens_out += reply.usage.output_tokens
        last = getattr(self.chat, "last_cost_usd", None)
        self.cost += (
            last
            if last is not None
            else cost_usd(
                reply.usage.input_tokens,
                reply.usage.output_tokens,
                self.price_usd_per_mtok,
            )
        )
        return reply


def request_hash(model: str, messages: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps([model, messages], sort_keys=True).encode()
    ).hexdigest()


def run_row(
    chat: Any,
    model: str,
    messages: list[dict[str, Any]],
    *,
    json_expected: bool,
    sleep: Callable[[float], None] | None,
) -> dict[str, Any]:
    meter = Metered(chat)
    started = time.monotonic()
    row: dict[str, Any] = {
        "model": model,
        "request_hash": request_hash(model, messages),
        "error": None,
    }
    parsed = None
    raw = ""
    retry = False
    try:
        if json_expected:
            parsed, last, retry = invoke_json(meter, messages, sleep=sleep)
            raw = last.content
        else:
            from milo.llm.retry import invoke_with_retry

            raw = invoke_with_retry(meter, messages, None, sleep=sleep).content
    except ProviderFailure as exc:
        row["error"] = exc.error
    row.update(
        raw=raw, parsed=parsed, retry=retry, calls=meter.calls, temperature=0.0,
        usage={"input": meter.tokens_in, "output": meter.tokens_out},
        cost_usd=meter.cost, latency_s=round(time.monotonic() - started, 3),
    )  # fmt: skip
    return row


def task_a_rows(
    chat: Any, model: str, convs: dict[str, Any], sleep: Any
) -> list[dict[str, Any]]:
    system = EXTRACT_PROMPT.read_text(encoding="utf-8").replace(
        "{{FIELDS}}", field_list()
    )
    rows = []
    for cid, conv in convs.items():
        history: list[dict[str, Any]] = []
        earlier: list[str] = []
        for t in conv["turns"]:
            expected = conv["expected"].get(t["turn"], [])
            messages = [
                {"role": "system", "content": system},
                *history,
                {"role": "user", "content": t["owner_text"]},
            ]
            row = run_row(chat, model, messages, json_expected=True, sleep=sleep)
            row.update(
                task="A", conv=cid, turn=t["turn"], owner_text=t["owner_text"],
                expected=expected, earlier_fields=list(earlier),
            )  # fmt: skip
            row["scores"] = rescore(row)
            rows.append(row)
            history.append({"role": "user", "content": t["owner_text"]})
            earlier += [f["field_id"] for f in expected]
    return rows


def task_b_context(gold: list[dict[str, Any]], embedder: Any) -> list[dict[str, Any]]:
    chunks = kb_chunks()
    cvecs = embedder.embed([c["text"] for c in chunks])
    qvecs = embedder.embed([g["question"] for g in gold])
    items = []
    for g, qv in zip(gold, qvecs, strict=True):
        top = sorted(range(len(chunks)), key=lambda i: -cosine(qv, cvecs[i]))[:TOP_K]
        ctx = [chunks[i] for i in top]
        quotes = [_collapse(e["quote"]) for e in g.get("gold_evidence") or []]
        gold_ids = [
            c["chunk_id"] for c in ctx if any(q in _collapse(c["text"]) for q in quotes)
        ]
        items.append(
            {
                "qid": g["qid"],
                "question": g["question"],
                "answerable": bool(quotes),
                "context": ctx,
                "gold_chunk_ids": gold_ids,
            }
        )
    return items


def task_b_rows(
    chat: Any, model: str, items: list[dict[str, Any]], sleep: Any
) -> list[dict[str, Any]]:
    template = REPLY_PROMPT.read_text(encoding="utf-8")
    rows = []
    for it in items:
        ctx = "\n\n".join(f"[{c['chunk_id']}] {c['text']}" for c in it["context"])
        messages = [
            {"role": "system", "content": template.replace("{{CONTEXT}}", ctx)},
            {"role": "user", "content": it["question"]},
        ]
        row = run_row(chat, model, messages, json_expected=False, sleep=sleep)
        row.update(
            task="B", qid=it["qid"], answerable=it["answerable"], reply=row["raw"],
            context_ids=[c["chunk_id"] for c in it["context"]], gold_chunk_ids=it["gold_chunk_ids"],
        )  # fmt: skip
        row["scores"] = rescore(row)
        rows.append(row)
    return rows


def planned_cost(
    pinned: dict[str, dict[str, Any]], convs: dict[str, Any], n_gold: int
) -> float:
    """Upper estimate from the token counter and catalogue prices, with the protocol's output sizes."""
    system = EXTRACT_PROMPT.read_text(encoding="utf-8")
    turns = sum(len(c["turns"]) for c in convs.values())
    in_a = (
        count_message_tokens([{"role": "system", "content": system + field_list()}])
        + 400
    )
    in_b = (
        count_message_tokens(
            [{"role": "system", "content": REPLY_PROMPT.read_text(encoding="utf-8")}]
        )
        + 1200
    )
    total = 0.0
    for p in pinned.values():
        total += turns * (in_a * p["prompt"] + 150 * p["completion"])
        total += n_gold * (in_b * p["prompt"] + 120 * p["completion"])
    return total * 1.25  # margin for history growth and the one JSON re-ask


def run_live(args: argparse.Namespace) -> int:
    if os.environ.get("MILO_LIVE") != "1":
        print(LIVE_REFUSAL, file=sys.stderr)
        return 2
    from milo.llm import cloud
    from milo.settings import Settings
    from milo.spend import SpendLedger

    cfg = Settings.from_env()
    if not cfg.openrouter_api_key:
        print("OPENROUTER_API_KEY is not set in the environment", file=sys.stderr)
        return 2
    if args.split == "test" and not common.final_mode():
        print(
            "the test split needs MILO_FINAL=1, which only `make final-test` sets (D149)",
            file=sys.stderr,
        )
        return 2
    catalogue = cloud.fetch_catalogue(
        cfg.openrouter_api_key
    )  # free call; the slugs are checked before spending
    verified = verify_slugs(catalogue)
    convs = load_conversations(args.split)
    gold = common.load_gold("dev") if args.split == "dev" else []
    plan = planned_cost(verified["pinned"], convs, len(gold))
    ledger = SpendLedger(ROOT / "logs" / "spend.jsonl", budget_usd=cfg.budget_usd)
    if plan > CAP_USD:
        print(
            f"planned spend {plan:.2f} USD is above the protocol cap {CAP_USD:.2f} USD",
            file=sys.stderr,
        )
        return 2
    ledger.check(plan)
    rows: list[dict[str, Any]] = []
    from milo.rag.embed import OpenRouterEmbedder

    # Batched (64 per request) through the one retry site: the provider caps an embedding batch at 100 texts.
    embedder = (
        OpenRouterEmbedder(
            cfg.openrouter_api_key, EMBEDDING_MODEL, price_usd_per_mtok=0.2
        )
        if gold
        else None
    )
    items = task_b_context(gold, embedder) if embedder else []
    for slug, p in verified["pinned"].items():
        chat = cloud.OpenRouterChat(
            slug,
            cfg.openrouter_api_key,
            (p["prompt"] * 1e6, p["completion"] * 1e6),
            ledger=ledger,
            purpose="e02",
        )
        rows += task_a_rows(chat, slug, convs, time.sleep)
        rows += task_b_rows(chat, slug, items, time.sleep)
    emb_cost = round(embedder.total_cost_usd, 6) if embedder else 0.0
    return write_result(args, rows, verified, plan, emb_cost)


def write_result(
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
    verified: dict[str, Any],
    plan: float,
    embedding_cost_usd: float = 0.0,
) -> int:
    metrics = compute_metrics(rows)
    decision = decide(rows, metrics, verified["reference"])
    inputs: dict[str, Path | str] = {
        "gold": common.GOLD_PATH, "protocol_yaml": PROTOCOL_YAML, "extract_prompt": EXTRACT_PROMPT,
        "reply_prompt": REPLY_PROMPT, "protocol_doc": PROTOCOL_DOC,
    }  # fmt: skip
    base = ROOT / "data" / "demo" / "conversations"
    ids = DEV_CONVERSATIONS if args.split == "dev" else TEST_CONVERSATIONS
    for cid in ids:
        inputs[f"conversation_{cid}"] = base / f"{cid}.jsonl"
        inputs[f"expected_{cid}"] = base / f"{cid}.expected.json"
    result = {
        **common.provenance(
            model=",".join(verified["pinned"]), seed=0, temperature=0.0,
            command="MILO_LIVE=1 python evals/chat/compare_chat.py --live", inputs=inputs,
        ),
        "seed_note": "no sampling; the bootstrap seed is 0",
        "reason": REASON,
        "split": args.split,
        "catalogue": verified,
        "planned_cost_usd": round(plan, 4),
        "embedding_cost_usd": embedding_cost_usd,
        "chat_cost_usd": round(sum(r.get("cost_usd") or 0.0 for r in rows), 6),
        "metrics": normalise_metrics(metrics),
        "decision": decision,
        "rows": rows,
    }  # fmt: skip
    out = Path(args.result) if args.result else RESULT_DIR / f"chat_{args.split}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp.replace(out)
    print(decision["line"])
    return 0


# ---------------------------------------------------------------- replay


def run_replay(args: argparse.Namespace) -> int:
    path = (
        Path(args.result)
        if args.result
        else (DEFAULT_RESULT if DEFAULT_RESULT.exists() else FIXTURE)
    )
    if not path.exists():
        print(f"no recorded result at {path}", file=sys.stderr)
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"]
    bad = [
        _key(r)
        for r in rows
        if json.loads(json.dumps(rescore(r))) != _roundtrip(r["scores"])
    ]
    metrics = normalise_metrics(compute_metrics(rows))
    decision = decide(rows, compute_metrics(rows), data["decision"]["reference"])
    problems = []
    if bad:
        problems.append(f"{len(bad)} row scores differ")
    if metrics != data["metrics"]:
        problems.append("metrics differ from the recorded metrics")
    if decision != json.loads(json.dumps(data["decision"])):
        problems.append("decision differs from the recorded decision")
    where = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    if problems:
        print(f"replay MISMATCH for {where}: " + "; ".join(problems), file=sys.stderr)
        return 1
    print(f"replay OK for {where}: {len(rows)} rows, {len(metrics)} models")
    print(decision["line"])
    return 0


def _roundtrip(x: Any) -> Any:
    return json.loads(json.dumps(x))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--replay",
        action="store_true",
        help="recompute from a recorded result (no network)",
    )
    mode.add_argument("--live", action="store_true", help="paid run; needs MILO_LIVE=1")
    ap.add_argument("--result", help="result file to read (replay) or write (live)")
    ap.add_argument("--split", choices=("dev", "test"), default="dev")
    args = ap.parse_args(argv)
    if args.replay:
        return run_replay(args)
    return run_live(args)  # no flag means live: it refuses without MILO_LIVE=1


if __name__ == "__main__":
    raise SystemExit(main())
