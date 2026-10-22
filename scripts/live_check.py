"""`make live-check`: one table for the whole live batch (integrator decision 44). No network, no key, no spend.

For each live run named in `evals/protocols/` it prints the command, the protocol file and the commit that last
touched it, the estimated and capped cost taken from the protocol, and whether the runner refuses without
MILO_LIVE=1. The refusal is tested by running the command with MILO_LIVE unset (each runner refuses before it
reads a key or opens a socket). A run whose runner is not built yet is listed as such and does not fail the check.
The estimates are checked against the protocol text, so a changed protocol number fails the check.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = "uv run python"


@dataclass(frozen=True)
class LiveRun:
    name: str
    command: str  # with MILO_LIVE=1, as the integrator types it
    protocol: str
    estimate_usd: float
    cap_usd: float
    cost_source: str  # a phrase that must appear in the protocol, next to the number
    runner: str | None  # path that must exist; None when the runner is not built yet


RUNS = [
    LiveRun(
        "E01c embedding comparison",
        f"MILO_LIVE=1 {PY} evals/retrieval/compare_embeddings.py",
        "evals/protocols/e01-rerun.md",
        0.02,
        0.05,
        "Estimate 0.02 USD, cap 0.05 USD",
        "evals/retrieval/compare_embeddings.py",
    ),
    LiveRun(
        "M1 dense retrieval (index + dev questions)",
        f"MILO_LIVE=1 {PY} evals/retrieval/run_retrieval.py",
        "evals/protocols/e02-chat-compare.md",
        0.003,
        0.003,
        "about 0.003 USD",
        "evals/retrieval/run_retrieval.py",
    ),
    LiveRun(
        "E02 chat-model comparison (dev and test)",
        f"MILO_LIVE=1 {PY} evals/chat/compare_chat.py --live",
        "evals/protocols/e02-chat-compare.md",
        0.80,
        1.20,
        "Estimate 0.80 USD, cap 1.20 USD",
        "evals/chat/compare_chat.py",
    ),
    LiveRun(
        "M3 tool selection, product model",
        f"MILO_LIVE=1 {PY} evals/agent/run_tools.py --model claude-sonnet-5.5 "
        '--price-in <usd/M> --price-out <usd/M> --max-cost 0.30 --reason "<why>"',
        "evals/protocols/m3-tools.md",
        0.08,
        0.30,
        "about 0.08 USD, cap 0.30 USD",
        "evals/agent/run_tools.py",
    ),
    LiveRun(
        "M4 attack evaluation",
        f"MILO_LIVE=1 {PY} evals/agent/run_attacks.py --live --model <D3_model> "
        '--price-in <usd/M> --price-out <usd/M> --max-cost 0.60 --reps 3 --reason "<why>"',
        "evals/protocols/m4-attacks.md",
        0.60,  # the protocol states only the cap; the estimate is taken as the cap (upper bound)
        0.60,
        "--max-cost 0.60",
        "evals/agent/run_attacks.py",
    ),
    LiveRun(
        "SR live-model cases (5)",
        "(no runner yet) evals/security live runner",
        "evals/protocols/sr-live.md",
        0.10,
        0.50,
        "about 0.10 USD, cap 0.50 USD",
        None,
    ),
    LiveRun(
        "M5G gate probes (Jev)",
        "(no runner yet) M5G gate probe runner",
        "evals/protocols/m5g-gate.md",
        0.05,
        0.20,
        "about 0.05 USD",
        None,
    ),
]


def protocol_commit(protocol: str) -> str:
    out = subprocess.run(
        ["git", "log", "-1", "--format=%h", "--", protocol],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip() or "uncommitted"


def refuses_without_live(run: LiveRun) -> tuple[bool, str]:
    """Run the command with MILO_LIVE unset; it must exit non-zero and say why."""
    assert run.runner is not None
    argv = shlex.split(run.command.removeprefix("MILO_LIVE=1 "))
    # placeholders for values the runner would take from the integrator; the refusal comes before they matter
    argv = [("1" if a == "<usd/M>" else "x" if a.startswith("<") else a) for a in argv]
    env = {k: v for k, v in os.environ.items() if k not in ("MILO_LIVE", "MILO_FINAL")}
    try:
        res = subprocess.run(
            argv,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "timed out"
    text = (res.stdout + res.stderr).strip().splitlines()
    ok = res.returncode != 0 and any("MILO_LIVE" in line for line in text)
    return ok, (text[-1][:90] if text else "no output")


def cost_in_protocol(run: LiveRun) -> bool:
    text = (ROOT / run.protocol).read_text(encoding="utf-8")
    return re.sub(r"\s+", " ", run.cost_source) in re.sub(r"\s+", " ", text)


def main() -> int:
    failures: list[str] = []
    total_est = total_cap = 0.0
    print(
        "MILO live batch check (no network, no key; each runner is run once with MILO_LIVE unset)\n"
    )
    for i, run in enumerate(RUNS, 1):
        commit = protocol_commit(run.protocol)
        built = run.runner is not None and (ROOT / run.runner).exists()
        if built:
            ok, why = refuses_without_live(run)
            refusal = (
                "REFUSES without MILO_LIVE=1" if ok else f"DOES NOT REFUSE ({why})"
            )
            if not ok:
                failures.append(f"{run.name}: {refusal}")
        else:
            refusal = "runner not built yet (cannot run)"
        if not cost_in_protocol(run):
            failures.append(f"{run.name}: protocol no longer says '{run.cost_source}'")
        total_est += run.estimate_usd
        total_cap += run.cap_usd
        print(f"{i}. {run.name}")
        print(f"   command   {run.command}")
        print(f"   protocol  {run.protocol} @ {commit}")
        print(
            f"   cost      estimate {run.estimate_usd:.3f} USD, cap {run.cap_usd:.3f} USD"
        )
        print(f"   refusal   {refusal}")
    print(f"\nTOTAL estimate {total_est:.3f} USD, total of caps {total_cap:.3f} USD")
    print(
        "(M4 and the retrieval run state no estimate of their own: M4 counts its cap, retrieval uses the E02 embedding figure.)"
    )
    unbuilt = [r.name for r in RUNS if r.runner is None]
    if unbuilt:
        print("NOT BUILT: " + "; ".join(unbuilt))
    for f in failures:
        print("FAIL: " + f, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
