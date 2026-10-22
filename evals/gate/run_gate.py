"""M5G gate runner (protocol `evals/protocols/m5g-gate.md`; SPEC D103 to D118, D56, D126; HARNESS rules 6, 11, 14).

Rows: the dev scope rows of `data/scope/scope_set.jsonl` and the probes of `data/scope/probes.jsonl` (the two `empty`
probes never reach the gate, `validate` rejects them first, and are not sent). Every row goes through stage 1; the
`undecided` ones get one Jev `noul` call. The thresholds come from the dev scope rows only, by the protocol's
order-statistic rule (`milo.gate.route.compute_thresholds`); routing is then recomputed from each row's P.

Modes
  --dry     (default) stage 2 answered by the committed scripted fixture `evals/gate/fixtures/jev_dry.json`
            (synthetic probabilities in the documented Decisions response shape; NOT a measurement). Offline, free.
            Writes evals/results/m5g/gate_dry.json and jev_replay_dry.jsonl. Never writes gate_dev.json, which the
            product reads its thresholds from.
  --replay  recompute stage 1, every P (re-parsed from the recorded raw responses), the thresholds and every metric
            from the stored files, and compare with the stored values; exit 1 on a difference. Default file:
            gate_dev.json when it exists, else gate_dry.json (or --file). Offline.
  --live    the dev probe run: MILO_LIVE=1, OPENROUTER_API_KEY, --reason; --max-cost defaults to the protocol cap
            0.20 USD. Writes gate_dev.json and jev_replay.jsonl. Stops at the cap (records kept, no gate_dev.json).
  --final   the single test-split run (protocol run 4): needs MILO_FINAL=1 and MILO_LIVE=1, a committed
            gate_dev.json (the frozen thresholds), and no gate_test.json yet. Writes gate_test.json and appends
            its records to jev_replay.jsonl.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.common import final_mode, provenance  # noqa: E402
from milo.gate.jev import (  # noqa: E402
    JEV_MODEL,
    JEV_PRICE_IN_USD_PER_MTOK,
    RUBRIC_VERSION,
    JevGate,
    build_state,
    parse_response,
    state_hash,
)
from milo.gate.route import (  # noqa: E402
    GATE_DEV_PATH,
    NO_THRESHOLDS,
    Gate,
    GateDecision,
    Thresholds,
    compute_thresholds,
    load_thresholds,
    route_p,
)
from milo.gate.stage1 import LEXICON_PATH, Stage1, load_lexicon  # noqa: E402

SCOPE_DIR = ROOT / "data" / "scope"
SCOPE_SET = SCOPE_DIR / "scope_set.jsonl"
PROBES = SCOPE_DIR / "probes.jsonl"
PROTOCOL = ROOT / "evals" / "protocols" / "m5g-gate.md"
FIXTURE = Path(__file__).with_name("fixtures") / "jev_dry.json"
RESULTS = ROOT / "evals" / "results" / "m5g"
DRY_PATH = RESULTS / "gate_dry.json"
DRY_REPLAY = RESULTS / "jev_replay_dry.jsonl"
DEV_PATH = GATE_DEV_PATH
DEV_REPLAY = RESULTS / "jev_replay.jsonl"
TEST_PATH = RESULTS / "gate_test.json"
CAP_USD = 0.20  # protocol: "Cap 0.20 USD"
BOOTSTRAP_RESAMPLES = 2000
SEED = 0
WHY = "stage-2 probability needed to set or confirm a threshold"


class Abort(RuntimeError):
    pass


# ---- data ----


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def scope_rows(split: str) -> list[dict[str, Any]]:
    """Scope rows of one split. `test` only under MILO_FINAL=1 (the test split is read once, protocol run 4)."""
    if split == "test" and not final_mode():
        raise PermissionError(
            "the scope test split is sealed; --final needs MILO_FINAL=1"
        )
    return [r for r in read_jsonl(SCOPE_SET) if r["split"] == split]


def gate_inputs(split: str, with_probes: bool) -> list[dict[str, Any]]:
    rows = [
        {
            "sid": r["sid"],
            "kind": "scope",
            "split": split,
            "label": r["label"],
            "style": r["style"],
            "text": r["text"],
            "recent_turns": r["recent_turns"],
        }
        for r in scope_rows(split)
    ]
    if with_probes:
        rows += [
            {
                "sid": p["sid"],
                "kind": "probe",
                "split": "probe",
                "category": p["category"],
                "expected": p["expected"],
                "text": p["text"],
                "recent_turns": p["recent_turns"],
            }
            for p in read_jsonl(PROBES)
        ]
    return rows


# ---- transports ----


class ScriptedTransport:
    """Dry stage 2: answers each request from the fixture by its state hash; never touches the network."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.by_hash = {e["state_hash"]: e for e in fixture["responses"].values()}
        self.last_response_model: str | None = None
        self.last_cost_usd: float | None = None

    def decide(self, body: dict[str, Any]) -> dict[str, Any]:
        entry = self.by_hash[state_hash(body["state"])]
        raw = entry["raw"]
        self.last_response_model = raw.get("model") if isinstance(raw, dict) else None
        usage = raw.get("usage") if isinstance(raw, dict) else None
        self.last_cost_usd = usage.get("cost") if isinstance(usage, dict) else None
        if entry.get("raise"):
            raise TimeoutError(entry["raise"])
        return dict(raw) if isinstance(raw, dict) else raw


# ---- the run ----


def run_rows(
    gate: Gate,
    rows: list[dict[str, Any]],
    *,
    cap: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Stage 1 for every row, stage 2 for the undecided; returns (result rows, replay records, stop reason)."""
    out: list[dict[str, Any]] = []
    replay: list[dict[str, Any]] = []
    spent = 0.0
    for r in rows:
        if r["kind"] == "probe" and r.get("category") == "empty":
            out.append(
                {
                    **_meta(r),
                    "stage1": None,
                    "stage1_reason": "not sent: validate rejects empty input",
                }
            )
            continue
        if cap is not None and spent >= cap:
            return out, replay, f"cap {cap:.2f} USD reached at {r['sid']}"
        d: GateDecision = gate.decide(r["text"], r["recent_turns"])
        spent += d.cost_usd
        row = {
            **_meta(r),
            "stage1": d.stage1,
            "stage1_reason": d.stage1_reason,
            "p": d.p,
            "gate_failed": d.gate_failed,
            "cost_usd": d.cost_usd if d.stage == 2 else 0.0,
            "latency_s": d.latency_s if d.stage == 2 else 0.0,
            "state_hash": d.state_hash,
        }
        out.append(row)
        if d.stage == 2:
            replay.append(
                {
                    "sid": r["sid"],
                    "split": r["split"],
                    "state_hash": d.state_hash,
                    "rubric_version": d.rubric_version,
                    "request": d.jev_request,
                    "raw": d.raw,
                    "model": d.jev_model,
                    "p": d.p,
                    "gate_failed": d.gate_failed,
                    "failure": d.failure,
                    "latency_s": d.latency_s,
                    "cost_usd": d.cost_usd,
                    "reason": WHY,
                }
            )
    return out, replay, None


def _meta(r: dict[str, Any]) -> dict[str, Any]:
    keys = ("sid", "kind", "split", "label", "style", "category", "expected")
    return {k: r[k] for k in keys if k in r}


# ---- metrics ----


def thresholds_from(rows: list[dict[str, Any]]) -> Thresholds:
    pts = [
        (r["sid"], r["label"], float(r["p"]))
        for r in rows
        if r["kind"] == "scope"
        and r.get("stage1") == "undecided"
        and r.get("p") is not None
    ]
    return compute_thresholds(pts)


def route_of(row: dict[str, Any], th: Thresholds) -> str | None:
    s1 = row.get("stage1")
    if s1 is None:
        return None
    if s1 in ("greeting", "pass", "refuse"):
        return str(s1)
    if row.get("gate_failed") or row.get("p") is None:
        return "band"
    return route_p(float(row["p"]), th)


def ci(xs: list[float]) -> list[float] | None:
    if not xs:
        return None
    rng = random.Random(SEED)
    n = len(xs)
    means = sorted(
        sum(xs[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(BOOTSTRAP_RESAMPLES)
    )
    return [
        round(means[int(0.025 * BOOTSTRAP_RESAMPLES)], 6),
        round(means[int(0.975 * BOOTSTRAP_RESAMPLES) - 1], 6),
    ]


def rate(xs: list[float]) -> dict[str, Any]:
    return {
        "n": len(xs),
        "rate": round(sum(xs) / len(xs), 6) if xs else None,
        "ci95": ci(xs),
    }


def p95(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)], 4)


def metrics(rows: list[dict[str, Any]], th: Thresholds) -> dict[str, Any]:
    scope = [r for r in rows if r["kind"] == "scope"]
    sent = [r for r in rows if r.get("stage1") is not None]
    for r in rows:
        r["route"] = route_of(r, th)
    ins = [r for r in scope if r["label"] == "in_scope"]
    outs = [r for r in scope if r["label"] == "out_of_scope"]

    def refused(r: dict[str, Any]) -> float:
        return 1.0 if r["route"] == "refuse" else 0.0

    by_style: dict[str, dict[str, Any]] = {}
    for key in sorted({(r["label"], r["style"]) for r in scope}):
        grp = [r for r in scope if (r["label"], r["style"]) == key]
        by_style[f"{key[0]}/{key[1]}"] = {
            "n": len(grp),
            "refused": int(sum(refused(r) for r in grp)),
            "stage1": dict(Counter(r["stage1"] for r in grp)),
        }
    stage2 = [r for r in sent if r["stage1"] == "undecided"]
    lat = [float(r["latency_s"]) for r in stage2 if not r.get("gate_failed")]
    cost = sum(float(r.get("cost_usd") or 0.0) for r in stage2)
    probes: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "routes": Counter(), "match": 0}
    )
    for r in rows:
        if r["kind"] != "probe" or r["route"] is None:
            continue
        cell = probes[r["category"]]
        cell["n"] += 1
        cell["routes"][r["route"]] += 1
        want = {"pass": ("pass", "greeting"), "refuse": ("refuse",), "band": ("band",)}[
            r["expected"]
        ]
        cell["match"] += int(r["route"] in want)
    return {
        "false_refusal_in_scope": rate([refused(r) for r in ins]),
        "refusal_out_of_scope": rate([refused(r) for r in outs]),
        "refused_by_stage": {
            "in_scope": {
                "stage1": sum(
                    1 for r in ins if r["route"] == "refuse" and r["stage1"] == "refuse"
                ),
                "stage2": sum(
                    1
                    for r in ins
                    if r["route"] == "refuse" and r["stage1"] == "undecided"
                ),
            },
            "out_of_scope": {
                "stage1": sum(
                    1
                    for r in outs
                    if r["route"] == "refuse" and r["stage1"] == "refuse"
                ),
                "stage2": sum(
                    1
                    for r in outs
                    if r["route"] == "refuse" and r["stage1"] == "undecided"
                ),
            },
        },
        "stage1_outcomes": {
            label: dict(Counter(r["stage1"] for r in scope if r["label"] == label))
            for label in ("in_scope", "out_of_scope")
        },
        "share_settled_by_stage1": round(
            sum(1 for r in scope if r["stage1"] != "undecided") / len(scope), 6
        )
        if scope
        else None,
        "routes": {
            label: dict(Counter(r["route"] for r in scope if r["label"] == label))
            for label in ("in_scope", "out_of_scope")
        },
        "stage2_calls": len(stage2),
        "stage2_cost_usd": round(cost, 8),
        "mean_cost_per_call_usd": round(cost / len(stage2), 10) if stage2 else None,
        "latency_s": {
            "mean": round(statistics.fmean(lat), 4) if lat else None,
            "p95": p95(lat),
        },
        "gate_failed": sum(1 for r in stage2 if r.get("gate_failed")),
        "by_style": by_style,
        "probes": {
            k: {"n": v["n"], "routes": dict(v["routes"]), "match_expected": v["match"]}
            for k, v in sorted(probes.items())
        },
        "cost_line": "stage-1 refusals cost 0; a stage-2 refusal costs its one gate call (D109 as amended)",
    }


# ---- writing and replay ----


def write_jsonl(
    path: Path, recs: list[dict[str, Any]], *, append: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w", encoding="utf-8") as fh:
        for rec in recs:
            fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")


def write_result(
    path: Path,
    *,
    mode: str,
    model: str,
    command: str,
    reason: str,
    rows: list[dict[str, Any]],
    th: Thresholds,
    replay_file: Path,
    computed_thresholds: bool,
) -> dict[str, Any]:
    lex = load_lexicon()
    doc = {
        **provenance(
            model=model,
            seed=SEED,
            temperature=0.0,
            command=command,
            inputs={
                "scope_set": SCOPE_SET,
                "probes": PROBES,
                "lexicon": LEXICON_PATH,
                "protocol": PROTOCOL,
            },
        ),
        "protocol": "m5g-gate",
        "mode": mode,
        "reason": reason,
        "lexicon_version": lex["version"],
        "rubric_version": RUBRIC_VERSION,
        "jev_model": JEV_MODEL,
        "jev_price_in_usd_per_mtok": JEV_PRICE_IN_USD_PER_MTOK,
        "replay_file": str(replay_file.relative_to(ROOT)),
        "thresholds_computed_here": computed_thresholds,
        "thresholds": th.as_dict(),
        "metrics": metrics(rows, th),
        "rows": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, indent=1, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return doc


def replay(path: Path) -> list[str]:
    """Recompute everything from the stored rows and replay records; return the differences."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    diffs: list[str] = []
    stage1 = Stage1(load_lexicon())
    rows = doc["rows"]
    for r in rows:
        if r.get("stage1") is None:
            continue
        src = _source_text(r)
        got = list(stage1.classify(src["text"]))
        if got != [r["stage1"], r["stage1_reason"]]:
            diffs.append(
                f"{r['sid']}: stage 1 {got} != stored {[r['stage1'], r['stage1_reason']]}"
            )
    recs = {(x["sid"], x["split"]): x for x in read_jsonl(ROOT / doc["replay_file"])}
    for r in rows:
        if r.get("stage1") != "undecided":
            continue
        rec = recs.get((r["sid"], r["split"]))
        if rec is None:
            diffs.append(f"{r['sid']}: no replay record")
            continue
        src = _source_text(r)
        state = build_state(
            src["text"], src["recent_turns"], rec["request"]["state"]["scope_summary"]
        )
        if (
            state_hash(state) != rec["state_hash"]
            or rec["state_hash"] != r["state_hash"]
        ):
            diffs.append(f"{r['sid']}: state hash differs")
        try:
            p: float | None = parse_response(rec["raw"])
        except ValueError:
            p = None
        if rec.get("gate_failed"):
            p = None
        if p != r.get("p"):
            diffs.append(f"{r['sid']}: P {p} != stored {r.get('p')}")
    if doc.get("thresholds_computed_here"):
        th = thresholds_from(rows)
    else:
        stored = doc["thresholds"]
        th = Thresholds(
            t_refuse=stored["t_refuse"],
            t_pass=stored["t_pass"],
            t_refuse_sid=stored["t_refuse_sid"],
            t_pass_sid=stored["t_pass_sid"],
            min_in=stored.get("min_in"),
            min_in_sid=stored.get("min_in_sid"),
            max_out=stored.get("max_out"),
            max_out_sid=stored.get("max_out_sid"),
            rubric_version=stored.get("rubric_version"),
        )
    if th.as_dict() != doc["thresholds"]:
        diffs.append(f"thresholds {th.as_dict()} != stored {doc['thresholds']}")
    fresh = json.loads(
        json.dumps([{k: v for k, v in r.items() if k != "route"} for r in rows])
    )
    m = json.loads(json.dumps(metrics(fresh, th)))
    if m != doc["metrics"]:
        for k in m:
            if m[k] != doc["metrics"].get(k):
                diffs.append(f"metric {k}: {m[k]} != stored {doc['metrics'].get(k)}")
    if [r.get("route") for r in fresh] != [r.get("route") for r in rows]:
        diffs.append("per-row routes differ")
    return diffs


_TEXTS: dict[tuple[str, str], dict[str, Any]] = {}


def _source_text(row: dict[str, Any]) -> dict[str, Any]:
    """The text and turns of a stored row, read again from the data (the result files keep no owner text twice)."""
    if not _TEXTS:
        for r in read_jsonl(SCOPE_SET):
            _TEXTS[("scope", r["sid"])] = r
        for p in read_jsonl(PROBES):
            _TEXTS[("probe", p["sid"])] = p
    return _TEXTS[(row["kind"], row["sid"])]


# ---- modes ----


def run_dry(out: Path | None = None) -> dict[str, Any]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # a fixed clock, so the dry result is byte-stable apart from its provenance
    jev = JevGate(ScriptedTransport(fixture), clock=lambda: 0.0)
    gate = Gate(jev=jev, thresholds=NO_THRESHOLDS)
    rows, recs, stop = run_rows(gate, gate_inputs("dev", with_probes=True))
    assert stop is None
    write_jsonl(DRY_REPLAY, recs)
    th = thresholds_from(rows)
    return write_result(
        out or DRY_PATH,
        mode="dry",
        model="dry-scripted (evals/gate/fixtures/jev_dry.json, synthetic probabilities)",
        command="uv run python evals/gate/run_gate.py --dry",
        reason="offline harness check: scripted stage-2 answers; no measurement",
        rows=rows,
        th=th,
        replay_file=DRY_REPLAY,
        computed_thresholds=True,
    )


def live_refusals(args: argparse.Namespace, env: dict[str, str]) -> list[str]:
    errs = []
    if env.get("MILO_LIVE") != "1":
        errs.append(
            "refusing to run: the Jev probes spend OpenRouter budget (D126) and need MILO_LIVE=1"
        )
    if not args.reason:
        errs.append("--reason is required (HARNESS rule 14)")
    if args.max_cost is not None and args.max_cost > CAP_USD:
        errs.append(f"--max-cost {args.max_cost} is above the protocol cap {CAP_USD}")
    if args.final and not final_mode():
        errs.append(
            "refusing to run: --final reads the sealed test split and needs MILO_FINAL=1"
        )
    return errs


def _live_gate(th: Thresholds) -> tuple[Gate, Any]:
    from milo.gate.jev import jev_from_env
    from milo.settings import Settings
    from milo.spend import SpendLedger

    cfg = Settings.from_env()
    ledger = SpendLedger(budget_usd=cfg.budget_usd)
    jev = jev_from_env(dict(os.environ), ledger=ledger)
    if jev is None:
        raise SystemExit("refusing to run: OPENROUTER_API_KEY is not set")
    return Gate(jev=jev, thresholds=th), ledger


def run_live(args: argparse.Namespace) -> int:
    cap = CAP_USD if args.max_cost is None else args.max_cost
    gate, ledger = _live_gate(NO_THRESHOLDS)
    ledger.check(cap)
    rows, recs, stop = run_rows(gate, gate_inputs("dev", with_probes=True), cap=cap)
    write_jsonl(DEV_REPLAY, recs)
    if stop:
        print(
            f"stopped: {stop}; {len(recs)} records kept in {DEV_REPLAY.name}, no gate_dev.json written"
        )
        return 1
    th = thresholds_from(rows)
    doc = write_result(
        args.out or DEV_PATH,
        mode="live",
        model=JEV_MODEL,
        command=f'MILO_LIVE=1 uv run python evals/gate/run_gate.py --live --max-cost {cap} --reason "<reason>"',
        reason=args.reason,
        rows=rows,
        th=th,
        replay_file=DEV_REPLAY,
        computed_thresholds=True,
    )
    served = sorted({str(r.get("model")) for r in recs})
    print(
        f"dev probe run: {len(recs)} Jev calls, models {served}, thresholds {doc['thresholds']}"
    )
    return 0


def run_final(args: argparse.Namespace) -> int:
    if TEST_PATH.exists():
        raise SystemExit(
            "refusing to run: gate_test.json exists; the test split is read once (protocol run 4)"
        )
    if not DEV_PATH.exists():
        raise SystemExit(
            "refusing to run: no committed gate_dev.json (run the dev probe run first)"
        )
    th = load_thresholds(DEV_PATH)
    dev = json.loads(DEV_PATH.read_text(encoding="utf-8"))
    cap = CAP_USD if args.max_cost is None else args.max_cost
    gate, ledger = _live_gate(th)
    inputs = gate_inputs("test", with_probes=False)
    per_call = dev["metrics"].get("mean_cost_per_call_usd") or 0.0
    projected = per_call * len(
        inputs
    )  # upper bound: every test row reaching stage 2 (D116)
    print(
        f"budget check: projected test cost <= {projected:.6f} USD; remaining {ledger.remaining():.4f} USD"
    )
    ledger.check(projected)
    rows, recs, stop = run_rows(gate, inputs, cap=cap)
    write_jsonl(DEV_REPLAY, recs, append=True)
    if stop:
        print(f"stopped: {stop}")
        return 1
    write_result(
        args.out or TEST_PATH,
        mode="final",
        model=JEV_MODEL,
        command='MILO_FINAL=1 MILO_LIVE=1 uv run python evals/gate/run_gate.py --final --reason "<reason>"',
        reason=args.reason,
        rows=rows,
        th=th,
        replay_file=DEV_REPLAY,
        computed_thresholds=False,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry", action="store_true")
    mode.add_argument("--replay", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--final", action="store_true")
    ap.add_argument("--file", type=Path, help="result file to replay")
    ap.add_argument("--out", type=Path, help="where to write the result")
    ap.add_argument("--max-cost", type=float, dest="max_cost")
    ap.add_argument("--reason")
    args = ap.parse_args(argv)

    if args.replay:
        path = args.file or (DEV_PATH if DEV_PATH.exists() else DRY_PATH)
        diffs = replay(path)
        for d in diffs[:20]:
            print("DIFF", d)
        print(
            f"replay {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}: "
            + ("ok" if not diffs else f"{len(diffs)} differences")
        )
        if path == DEV_PATH and TEST_PATH.exists():
            more = replay(TEST_PATH)
            print(
                f"replay {TEST_PATH.name}: "
                + ("ok" if not more else f"{len(more)} differences")
            )
            diffs += more
        return 0 if not diffs else 1
    if args.live or args.final:
        errs = live_refusals(args, dict(os.environ))
        if errs:
            print("; ".join(errs))
            return 2
        return run_final(args) if args.final else run_live(args)
    doc = run_dry(args.out)
    m = doc["metrics"]
    print(
        f"dry run: {m['stage2_calls']} stage-2 calls, false refusal {m['false_refusal_in_scope']['rate']}, "
        f"out-of-scope refused {m['refusal_out_of_scope']['rate']}, thresholds {doc['thresholds']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
