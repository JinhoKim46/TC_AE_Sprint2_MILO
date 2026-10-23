"""Pre-live runner pass (integrator decision 44). Offline: every client is a scripted fake."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pytest

from milo.errors import ProviderFailure
from milo.llm import cloud
from milo.rag.embed import OpenRouterEmbedder
from milo.types import ChatReply, ToolCall, Usage

ROOT = Path(__file__).resolve().parents[1]


class HttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"http {status}")
        self.status_code = status


class EmbClient:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[int] = []
        self.embeddings = NS(create=self._create)

    def _create(self, model: str, input: list[str]) -> Any:
        self.requests.append(len(input))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return NS(
            data=[NS(embedding=[1.0, 0.0], index=i) for i, _ in enumerate(input)],
            usage=item,
        )


# ---- embedding usage and the single retry site ----


def test_embedding_usage_cost_field_wins_over_price() -> None:
    client = EmbClient([NS(total_tokens=100, cost=0.5), NS(total_tokens=50, cost=None)])
    emb = cloud.OpenRouterEmbeddings("k", client=client, price_usd_per_mtok=200_000.0)
    emb.embed(["a"])
    assert (emb.last_tokens, emb.last_cost_usd) == (100, 0.5)
    emb.embed(["b"])
    assert emb.last_cost_usd == pytest.approx(10.0)  # 50 tokens at the fallback price
    assert emb.total_tokens == 150 and emb.total_cost_usd == pytest.approx(10.5)


def test_embedding_cost_unknown_is_counted_not_invented() -> None:
    emb = cloud.OpenRouterEmbeddings("k", client=EmbClient([None]))
    emb.embed(["a"])
    assert emb.last_cost_usd is None and emb.cost_unknown_calls == 1
    assert emb.total_cost_usd == 0.0


def test_embedder_retries_a_batch_at_the_one_site() -> None:
    slept: list[float] = []
    client = EmbClient([HttpError(503), HttpError(429), NS(total_tokens=7, cost=0.001)])
    emb = OpenRouterEmbedder("k", client=client, sleep=slept.append)
    assert len(emb.embed(["x"])) == 1
    assert client.requests == [1, 1, 1] and slept == [0.5, 1.0]
    assert (
        emb.calls == 1 and emb.total_tokens == 7
    )  # only the successful attempt counts


def test_embedder_permanent_error_not_retried_and_exhaustion_is_typed() -> None:
    client = EmbClient([HttpError(401)])
    with pytest.raises(ProviderFailure):
        OpenRouterEmbedder("k", client=client, sleep=lambda _s: None).embed(["x"])
    assert client.requests == [1]
    client = EmbClient([HttpError(500)] * 3)
    with pytest.raises(ProviderFailure):
        OpenRouterEmbedder("k", client=client, sleep=lambda _s: None).embed(["x"])
    assert client.requests == [1, 1, 1]


def test_compare_embeddings_has_no_direct_provider_call() -> None:
    text = (ROOT / "evals/retrieval/compare_embeddings.py").read_text()
    assert "openrouter" + ".ai" not in text and "urllib" not in text


def test_compare_embeddings_reads_its_arms_from_the_protocol() -> None:
    sys.path.insert(0, str(ROOT / "evals" / "retrieval"))
    try:
        import compare_embeddings as ce
    finally:
        sys.path.pop(0)
    slugs = [m for m, _ in ce.MODELS]
    assert len(slugs) == 5 and ce.REFERENCE == slugs[0] == "google/gemini-embedding-2"
    assert ce.CHUNK_CONFIGS == [(800, 100), (400, 50), (1500, 150)]
    assert ce.RESULT_NAME == "embedding_dev_v3.json"


# ---- run_tools live ----


def _tools() -> Any:
    from evals.agent import run_tools

    return run_tools


def _args(tmp_path: Path, **kw: Any) -> Any:
    base = {
        "model": "claude-sonnet-5.5",
        "price_in": 3.0,
        "price_out": 15.0,
        "max_cost": 0.30,
        "reason": "test",
        "out": tmp_path / "tools_live.json",
    }
    return NS(**{**base, **kw})


class FakeChat:
    """Stands in for OpenRouterChat: emits each case's expected calls, one cost figure per call."""

    fail_first = False
    cost = 0.001

    def __init__(self, model: str, key: str, price: Any, **kw: Any) -> None:
        self.model, self.price_usd_per_mtok = model, price
        self.temperature = kw.get("temperature")
        self.last_cost_usd: float | None = None
        self.last_response_model: str | None = None
        self.calls = 0

    def invoke(self, messages: list[dict[str, Any]], tools: Any = None) -> ChatReply:
        self.calls += 1
        if FakeChat.fail_first and self.calls == 1:
            raise HttpError(503)
        query = messages[-1]["content"]
        case = next(c for c in _tools().load_cases() if c["query"] == query)
        self.last_cost_usd, self.last_response_model = self.cost, "anthropic/fake-2026"
        calls = [
            ToolCall(f"c{i}", c["name"], json.loads(json.dumps(c["args"])))
            for i, c in enumerate(case["expected_calls"])
        ]
        return ChatReply(content="", tool_calls=calls, usage=Usage(10, 5))


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MILO_LIVE", "1")
    monkeypatch.setattr(cloud, "OpenRouterChat", FakeChat)
    from milo.settings import Settings

    monkeypatch.setattr(
        Settings,
        "from_env",
        classmethod(lambda cls: NS(openrouter_api_key="k", budget_usd=100.0)),
    )
    FakeChat.fail_first, FakeChat.cost = False, 0.001


def test_tools_live_refuses_without_milo_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MILO_LIVE", raising=False)
    with pytest.raises(SystemExit, match="MILO_LIVE=1"):
        _tools().run_live(_args(tmp_path))


def test_tools_live_needs_max_cost(live: None, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--max-cost"):
        _tools().run_live(_args(tmp_path, max_cost=None))


def test_tools_live_refuses_when_cases_differ_from_manifest(
    live: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt = _tools()
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text(json.dumps({"data/tool_cases.jsonl": "0" * 64}))
    monkeypatch.setattr(rt, "MANIFEST_PATH", manifest)
    with pytest.raises(SystemExit, match="differs from data/MANIFEST.json"):
        rt.run_live(_args(tmp_path))
    assert not (tmp_path / "tools_live.json").exists()


def test_tools_live_writes_result_replay_file_latency_and_real_temperature(
    live: None, tmp_path: Path
) -> None:
    rt = _tools()
    FakeChat.fail_first = (
        True  # the first attempt of the first case is a 503, retried inside run_turn
    )
    args = _args(tmp_path)
    doc = rt.run_live(args)
    assert doc["temperature"] == 0.0 and doc["model"] == "claude-sonnet-5.5"
    assert doc["manifest_tool_cases_sha256"] == doc["inputs"]["tool_cases"]
    assert doc["response_models"] == ["anthropic/fake-2026"]
    rows = doc["rows"]
    import json as _json

    dev_cases = [
        r
        for r in (
            _json.loads(x)
            for x in (rt.ROOT / "data" / "tool_cases.jsonl").read_text().splitlines()
            if x.strip()
        )
        if r.get("split", "dev") == "dev"
    ]
    assert len(rows) == len(dev_cases) and all(
        "latency_s" in r and r["usage"] for r in rows
    )
    assert doc["metrics"]["p95_latency_s"] is not None
    assert doc["metrics"]["total_cost_usd"] == pytest.approx(0.001 * len(dev_cases))  # fake cost 0.001 per case
    replay = [
        json.loads(line)
        for line in (tmp_path / "tools_live_replay.jsonl").read_text().splitlines()
    ]
    assert len(replay) == 16  # 15 cases plus the failed attempt
    assert [r["attempt"] for r in replay[:2]] == [1, 2] and replay[0]["error"]
    assert replay[1]["response"]["response_model"] == "anthropic/fake-2026"
    assert {"request_hash", "request", "usage", "cost_usd", "latency_s"} <= set(
        replay[1]
    )
    assert rows[0]["attempts"] == 2
    stored = json.loads((tmp_path / "tools_live.json").read_text())
    assert (
        rt.compute_metrics(stored["rows"]) == stored["metrics"]
    )  # replay recomputes it exactly


def test_tools_live_stops_at_max_cost_and_writes_no_result(
    live: None, tmp_path: Path
) -> None:
    rt = _tools()
    FakeChat.cost = 0.1
    with pytest.raises(SystemExit, match="cost cap reached"):
        rt.run_live(_args(tmp_path, max_cost=0.25))
    assert not (tmp_path / "tools_live.json").exists()
    paid = (tmp_path / "tools_live_replay_aborted.jsonl").read_text().splitlines()
    assert (
        len(paid) == 3
    )  # the cap is checked before each call: 0.1, 0.2, 0.3 spent, then stop


def test_old_dry_rows_still_replay_after_the_metric_change() -> None:
    rt = _tools()
    assert rt.replay([rt.DRY_PATH]) == 0
