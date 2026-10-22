"""Replay every stored eval result and recompute its metrics (SPEC 10.3, `make eval`). No model call, no network.

Each runner's own `--replay` recomputes its numbers from the stored rows and must match. The run fails when any
replay fails or when a listed runner is missing. Optional runners (milestones that may not have merged) are
replayed when their script exists.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (name, script, optional)
RUNNERS: list[tuple[str, str, bool]] = [
    ("tools", "evals/agent/run_tools.py", False),
    ("attacks", "evals/agent/run_attacks.py", False),
    ("retrieval", "evals/retrieval/run_retrieval.py", False),
    ("ab", "evals/retrieval/ab.py", False),
    ("chat", "evals/chat/compare_chat.py", False),
    ("rag", "evals/rag/run_rag_eval.py", False),
    ("discovery", "evals/discovery/run_discovery.py", True),
    ("gate", "evals/gate/run_gate.py", True),
]


def run_one(script: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, script, "--replay"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    out = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode, "\n".join(out[-3:])


def main() -> int:
    failed: list[str] = []
    rows: list[tuple[str, str]] = []
    for name, script, optional in RUNNERS:
        if not (ROOT / script).exists():
            if optional:
                rows.append((name, "skipped (not built)"))
                continue
            rows.append((name, f"MISSING {script}"))
            failed.append(name)
            continue
        code, tail = run_one(script)
        print(f"== {name}: {script} --replay\n{tail}")
        rows.append((name, "ok" if code == 0 else f"FAILED (exit {code})"))
        if code != 0:
            failed.append(name)
    print("\nreplay summary")
    for name, status in rows:
        print(f"  {name:<10}{status}")
    if failed:
        print(f"make eval: {len(failed)} failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("make eval: every stored eval replays")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
