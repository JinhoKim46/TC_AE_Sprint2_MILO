"""Leakage checks (SPEC 10.1, HARNESS rule 6, SPEC 17 M0). Static, offline.

- No file under src/, evals/ (except evals/common.py) or tests/ (except this file and test_checker.py) names the gold
  file. The name is built from parts here so that this file does not name it either.
- No eval reads the test split: `load_gold` is called with no argument or "dev" everywhere outside `evals/final/`,
  and nothing outside `evals/final/` and `evals/common.py` assigns MILO_FINAL.
- Only the `final-test` Makefile target sets MILO_FINAL.
- The gold file still matches the manifest and has the 120-row split counts.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD_NAME = "gold" + "." + "jsonl"
FINAL_FLAG = "MILO" + "_FINAL"
EXCLUDED = {
    ROOT / "evals" / "common.py",
    ROOT / "tests" / "test_leakage.py",
    ROOT / "tests" / "test_checker.py",
}
TEXT_SUFFIXES = {".py", ".sh", ".toml", ".yaml", ".yml", ".cfg", ".ini"}


def _files(*roots: str, suffixes: set[str]) -> list[Path]:
    out = []
    for r in roots:
        for p in sorted((ROOT / r).rglob("*")):
            if (
                p.is_file()
                and p.suffix in suffixes
                and "__pycache__" not in p.parts
                and ".venv" not in p.parts
            ):
                out.append(p)
    return out


def test_only_common_names_the_gold_file():
    offenders = [
        str(p.relative_to(ROOT))
        for p in _files("src", "evals", "tests", suffixes=TEXT_SUFFIXES)
        if p not in EXCLUDED and GOLD_NAME in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"files naming the gold file: {offenders}"
    assert GOLD_NAME in (ROOT / "evals" / "common.py").read_text(encoding="utf-8")
    conftest = (ROOT / "conftest.py").read_text(encoding="utf-8")
    assert GOLD_NAME not in conftest


def _load_gold_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name == "load_gold":
            yield node


def test_no_eval_reads_the_test_split():
    bad = []
    for p in _files("src", "evals", suffixes={".py"}):
        if (
            p == ROOT / "evals" / "common.py"
            or "final" in p.relative_to(ROOT).parts[:2]
        ):
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for call in _load_gold_calls(tree):
            args = list(call.args) + [k.value for k in call.keywords]
            if not args:
                continue
            first = args[0]
            if not (isinstance(first, ast.Constant) and first.value == "dev"):
                bad.append(f"{p.relative_to(ROOT)}:{call.lineno}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == FINAL_FLAG:
                if p != ROOT / "src" / "milo" / "settings.py":
                    bad.append(
                        f"{p.relative_to(ROOT)}:{node.lineno} names {FINAL_FLAG}"
                    )
    assert bad == [], f"possible test-split reads: {bad}"


def test_only_final_test_target_sets_the_flag():
    lines = (ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    target = None
    for line in lines:
        if line and not line.startswith(("\t", " ", "#")) and ":" in line:
            target = line.split(":", 1)[0]
        if FINAL_FLAG in line and not line.lstrip().startswith("#"):
            assert target == "final-test", f"{FINAL_FLAG} set in target {target}"


def test_scanner_catches_a_test_split_read():
    tree = ast.parse('from evals.common import load_gold\nrows = load_gold("test")\n')
    (call,) = list(_load_gold_calls(tree))
    assert call.args[0].value == "test"


def test_gold_matches_manifest_and_counts():
    gold_path = ROOT / "data" / GOLD_NAME
    rows = [
        json.loads(line)
        for line in gold_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 120
    assert sum(r["split"] == "dev" for r in rows) == 60
    assert sum(r["split"] == "test" for r in rows) == 60
    assert len({r["qid"] for r in rows}) == 120
    manifest = json.loads((ROOT / "data" / "MANIFEST.json").read_text(encoding="utf-8"))
    import hashlib

    digest = hashlib.sha256(gold_path.read_bytes()).hexdigest()
    assert digest in json.dumps(manifest)
