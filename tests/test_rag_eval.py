"""M7 offline tests: sampler, kappa, label validator, dry replay, live refusals, the D26 order check, the adapter."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from evals import common
from evals.rag import generate as gen
from evals.rag import judge as jd
from evals.rag import run_rag_eval as run
from evals.rag import sample as smp
from evals.rag import schema, stats

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- sampler


def test_sample_is_seeded_stratified_dev_only_and_committed() -> None:
    dev = common.load_gold("dev")
    a, b = smp.draw(dev), smp.draw(dev)
    assert a == b and len(a) == 30 and len({r["qid"] for r in a}) == 30
    kinds = [r["type"] for r in a]
    assert {t: kinds.count(t) for t in set(kinds)} == {
        "paraphrase": 18,
        "identifier": 6,
        "unanswerable": 6,
    }
    assert {r["qid"] for r in a} <= {r["qid"] for r in dev}
    assert smp.draw(dev, seed=1) != a
    stored = json.loads(run.SAMPLE.read_text(encoding="utf-8"))
    assert stored["rows"] == a, "evals/rag/sample_dev.json is stale; rerun --sample"
    assert stored["split"] == "dev"


def test_sample_refuses_a_non_dev_row() -> None:
    with pytest.raises(PermissionError):
        smp.draw(
            [{"qid": "x1", "type": "paraphrase", "question": "q", "split": "test"}], 1
        )


def test_allocation_sums_to_n() -> None:
    assert smp.allocate({"a": 36, "b": 12, "c": 12}, 30) == {"a": 18, "b": 6, "c": 6}
    assert sum(smp.allocate({"a": 7, "b": 5, "c": 3}, 4).values()) == 4


# ---------------------------------------------------------------- kappa


def test_kappa_known_values() -> None:
    assert stats.kappa([1, 1, 0, 0], [1, 1, 0, 0], (0, 1)) == 1.0
    assert stats.kappa([1, 1, 0, 0], [1, 0, 0, 0], (0, 1)) == pytest.approx(0.5)
    assert (
        stats.kappa([1, 1, 1], [1, 1, 1], (0, 1)) is None
    )  # chance agreement is total
    assert stats.kappa(
        [0, 1, 2, 2], [0, 1, 2, 1], (0, 1, 2), weighted=True
    ) == pytest.approx(0.8)
    res = stats.kappa_ci([([1], [1]), ([0], [0]), ([1], [0]), ([0], [0])] * 5, (0, 1))
    assert res["kappa"] == pytest.approx(0.5)
    assert res["ci"][0] < res["kappa"] < res["ci"][1] + 1e-9
    assert stats.kappa_ci([([1], [1])], (0, 1))["kappa"] is None


# ---------------------------------------------------------------- labels validator


GOOD = {
    "qid": "q001",
    "faithfulness": 1,
    "relevance": 2,
    "context_relevance": [1, 0],
    "labeller": "agent-x",
}


def _write(path: Path, rows: list[Any], raw: str | None = None) -> Path:
    path.write_text(
        raw if raw is not None else "".join(json.dumps(r) + "\n" for r in rows),
        encoding="utf-8",
    )
    return path


def test_validator_accepts_a_good_file(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "labels.jsonl",
        [GOOD, {**GOOD, "qid": "q002", "context_relevance": []}],
    )
    rows, problems = schema.validate_labels(p, ["q001", "q002"], {"q001": 2, "q002": 0})
    assert problems == [] and len(rows) == 2


@pytest.mark.parametrize(
    ("rows", "raw", "needle"),
    [
        ([GOOD], None, "missing label for sampled qid q002"),
        ([GOOD, GOOD, {**GOOD, "qid": "q002"}], None, "duplicate qid"),
        (
            [GOOD, {**GOOD, "qid": "q002"}, {**GOOD, "qid": "q999"}],
            None,
            "not in the sample",
        ),
        (
            [{**GOOD, "faithfulness": 2}, {**GOOD, "qid": "q002"}],
            None,
            "faithfulness must be one of",
        ),
        (
            [{**GOOD, "relevance": 3}, {**GOOD, "qid": "q002"}],
            None,
            "relevance must be one of",
        ),
        (
            [{**GOOD, "relevance": None}, {**GOOD, "qid": "q002"}],
            None,
            "relevance must be one of",
        ),
        (
            [{**GOOD, "faithfulness": True}, {**GOOD, "qid": "q002"}],
            None,
            "faithfulness must be one of",
        ),
        (
            [{**GOOD, "context_relevance": [1]}, {**GOOD, "qid": "q002"}],
            None,
            "retrieved 2 contexts",
        ),
        (
            [{**GOOD, "context_relevance": [1, 2]}, {**GOOD, "qid": "q002"}],
            None,
            "list of 0 or 1",
        ),
        ([{**GOOD, "extra": 1}, {**GOOD, "qid": "q002"}], None, "unknown field"),
        (
            [
                {k: v for k, v in GOOD.items() if k != "labeller"},
                {**GOOD, "qid": "q002"},
            ],
            None,
            "missing field 'labeller'",
        ),
        ([], '{"qid": "q001", \nnot json\n', "not valid JSON"),
    ],
)
def test_validator_rejects_a_malformed_file(
    tmp_path: Path, rows: list[Any], raw: str | None, needle: str
) -> None:
    p = _write(tmp_path / "labels.jsonl", rows, raw)
    _, problems = schema.validate_labels(p, ["q001", "q002"], {"q001": 2, "q002": 2})
    assert any(needle in x for x in problems), problems


def test_validator_rejects_a_missing_file_and_the_unfilled_template(
    tmp_path: Path,
) -> None:
    assert schema.validate_labels(tmp_path / "none.jsonl", ["q001"])[1]
    tpl = _write(tmp_path / "t.jsonl", schema.template_rows(["q001"], {"q001": 2}))
    assert schema.validate_labels(tpl, ["q001"], {"q001": 2})[1]


def test_committed_template_lists_the_sample_qids() -> None:
    rows, problems = schema.load_rows(schema.TEMPLATE)
    assert problems == [] and [r["qid"] for r in rows] == run.sample_qids()
    assert all(r["faithfulness"] is None and r["relevance"] is None for r in rows)


def test_committed_labels_if_present_are_valid() -> None:
    if schema.LABELS.exists():
        assert schema.validate_labels(schema.LABELS, run.sample_qids())[1] == []


# ---------------------------------------------------------------- judge parsing and dry replay


def test_parse_reply_contract() -> None:
    assert jd.parse_reply("faithfulness", '{"score": 1, "reason": "x"}', 3) == 1
    assert jd.parse_reply("faithfulness", '```json\n{"score": 0}\n```', 3) == 0
    assert jd.parse_reply("faithfulness", '{"score": 2}', 3) is None
    assert jd.parse_reply("relevance", '{"score": 2}', 3) == 2
    assert jd.parse_reply("relevance", "not json", 3) is None
    assert jd.parse_reply("context_precision", '{"scores": [1, 0]}', 2) == [1, 0]
    assert jd.parse_reply("context_precision", '{"scores": [1]}', 2) is None
    assert jd.parse_reply("faithfulness", '{"score": true}', 1) is None


def test_placeholder_prompts_are_detected() -> None:
    for name in jd.PROMPT_NAMES.values():
        text = (run.PROMPTS / name).read_text(encoding="utf-8")
        assert jd.is_placeholder(text) or "{{answer}}" in text


def test_dry_replay_matches_and_detects_tampering(tmp_path: Path) -> None:
    assert run.replay_judge_file(run.DRY_RESULT)
    doc = json.loads(run.DRY_RESULT.read_text(encoding="utf-8"))
    doc["rows"][0]["judges"]["judge-beta"]["faithfulness"]["score"] = 0
    bad = tmp_path / "rag_dry.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    assert not run.replay_judge_file(bad)
    doc = json.loads(run.DRY_RESULT.read_text(encoding="utf-8"))
    for k in (
        "git_commit",
        "model",
        "seed",
        "temperature",
        "command",
        "timestamp",
        "inputs",
        "rows",
    ):
        assert k in doc
    assert (
        doc["summary"]["judge-beta"]["unparsed"] == 3
    )  # d09 is scripted as "not json" for every metric


def test_dry_result_is_stable() -> None:
    fresh = run.run_dry()
    stored = json.loads(run.DRY_RESULT.read_text(encoding="utf-8"))
    for key in ("summary", "inter_judge", "label_means", "decision"):
        assert json.loads(json.dumps(fresh[key])) == stored[key]


# ---------------------------------------------------------------- live refusals


def test_live_modes_refuse_without_milo_live(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MILO_LIVE", raising=False)
    full = ["--max-cost", "0.4", "--reason", "x"]
    assert (
        run.main(
            [
                "--generate",
                *full,
                "--product-model",
                "m",
                "--price-in",
                "1",
                "--price-out",
                "1",
            ]
        )
        == 2
    )
    assert run.main(["--judge", "a,1,1", "--judge", "b,1,1", *full]) == 2
    assert "MILO_LIVE=1" in capsys.readouterr().err


def test_live_modes_refuse_without_cap_or_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MILO_LIVE", "1")
    assert (
        run.main(
            [
                "--generate",
                "--product-model",
                "m",
                "--price-in",
                "1",
                "--price-out",
                "1",
            ]
        )
        == 2
    )
    assert run.main(["--judge", "a,1,1", "--judge", "b,1,1", "--max-cost", "1"]) == 2


def test_judge_refuses_without_labels_and_final_prompts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MILO_LIVE", "1")
    monkeypatch.setattr(run, "GEN_RESULT", tmp_path / "generation_dev.json")
    rc = run.main(
        ["--judge", "a,1,1", "--judge", "b,1,1", "--max-cost", "1", "--reason", "x"]
    )
    assert rc == 2 and "must exist first" in capsys.readouterr().err
    gen_doc = {"rows": [{"qid": q, "contexts": []} for q in run.sample_qids()]}
    (tmp_path / "generation_dev.json").write_text(json.dumps(gen_doc), encoding="utf-8")
    monkeypatch.setattr(schema, "LABELS", tmp_path / "labels.jsonl")
    rc = run.main(
        ["--judge", "a,1,1", "--judge", "b,1,1", "--max-cost", "1", "--reason", "x"]
    )
    err = capsys.readouterr().err
    assert rc == 2 and "labels.jsonl does not exist" in err and "placeholder" in err


# ---------------------------------------------------------------- D26 order by git


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


def _commit(repo: Path, rel: str, text: str) -> None:
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-m", f"add {rel}")


def test_order_check_labels_before_prompts(tmp_path: Path) -> None:
    repo = tmp_path / "ok"
    repo.mkdir()
    _git(repo, "init", "-q")
    labels, prompt = repo / "labels.jsonl", repo / "p.md"
    assert jd.order_problems(repo, labels, [prompt])  # nothing committed
    _commit(repo, "labels.jsonl", "{}\n")
    _commit(repo, "p.md", "final text")
    assert jd.order_problems(repo, labels, [prompt]) == []
    _commit(
        repo, "labels.jsonl", "{}\n{}\n"
    )  # editing the labels later does not reorder the first commit
    assert jd.order_problems(repo, labels, [prompt]) == []


def test_order_check_flags_prompt_before_labels_and_same_commit(tmp_path: Path) -> None:
    repo = tmp_path / "bad"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, "p.md", "final text")
    _commit(repo, "labels.jsonl", "{}\n")
    assert jd.order_problems(repo, repo / "labels.jsonl", [repo / "p.md"])
    repo2 = tmp_path / "same"
    repo2.mkdir()
    _git(repo2, "init", "-q")
    (repo2 / "labels.jsonl").write_text("{}\n")
    (repo2 / "p.md").write_text("x")
    _git(repo2, "add", ".")
    _git(repo2, "commit", "-m", "both")
    assert jd.order_problems(repo2, repo2 / "labels.jsonl", [repo2 / "p.md"])


# ---------------------------------------------------------------- generation adapter and cap


def test_app_ask_returns_the_pipeline_contexts_and_answer() -> None:
    from milo.app import build_app
    from milo.rag.embed import FakeEmbedder
    from milo.rag.retrieve import Retriever
    from milo.tools import MiloBackend
    from milo.types import ChatReply, Document, Usage

    class Chat:
        price_usd_per_mtok = (1.0, 1.0)

        def invoke(
            self, messages: list[dict[str, Any]], tools: Any = None
        ) -> ChatReply:
            return ChatReply("scripted answer", usage=Usage(10, 5))

    emb = FakeEmbedder()
    retr = Retriever(emb, min_score=-1.0)
    retr.ingest([Document("solutions/x", "Alpha desk never takes card payments.", {})])
    app = build_app(Chat(), emb, MiloBackend(retriever=retr), vector_store=retr)
    g = gen.app_ask(app)("Does alpha desk take card payments?")
    assert g.answer == "scripted answer" and g.error is None
    assert (
        g.contexts
        and g.contexts[0]["doc_id"] == "solutions/x"
        and "card payments" in g.contexts[0]["text"]
    )


def test_generate_stops_at_the_cap_between_questions() -> None:
    sample = [
        {"qid": f"q{i}", "type": "paraphrase", "question": f"q{i}"} for i in range(5)
    ]

    def ask(q: str) -> gen.Generated:
        ctx = [{"chunk_id": "c", "doc_id": "d", "score": 1.0, "text": "t"}]
        return gen.Generated("a", ctx, cost_usd=0.2)

    rows, partial = gen.generate(sample, ask, 0.5)
    assert partial and len(rows) == 3
    rows, partial = gen.generate(sample, ask, None)
    assert not partial and len(rows) == 5
