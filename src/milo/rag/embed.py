"""Embedders (SPEC D5, D126). `OpenRouterEmbedder` goes through `milo.llm.cloud`; `FakeEmbedder` is for tests,
the dry harness and replay: deterministic, no model, no network.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Any

from milo.errors import ProviderFailure
from milo.llm import cloud
from milo.llm.retry import classify_exception
from milo.settings import Settings

BATCH = 64  # texts per embedding request (as in E01)
_TOKEN = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    """Hashed bag-of-words, L2-normalised. The same text always gives the same vector."""

    model = "fake-hash-256"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in _TOKEN.findall(text.lower()):
            h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:4], "big")
            v[h % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v] if n else v


class OpenRouterEmbedder:
    """Batches texts and calls `cloud.OpenRouterEmbeddings`. One attempt per batch; a failure is typed."""

    def __init__(
        self,
        api_key: str,
        model: str = cloud.DEFAULT_EMBEDDING_MODEL,
        *,
        batch: int = BATCH,
        client: Any = None,
    ) -> None:
        self.model = model
        self.batch = batch
        self.calls = 0
        self._inner = cloud.OpenRouterEmbeddings(api_key, model, client=client)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            try:
                out += self._inner.embed(texts[i : i + self.batch])
            except ProviderFailure:
                raise
            except Exception as exc:
                raise ProviderFailure(classify_exception(exc)) from exc
            self.calls += 1
        return out


def live_embedder(model: str = cloud.DEFAULT_EMBEDDING_MODEL) -> OpenRouterEmbedder:
    """The OpenRouter embedder from the process environment. Refuses without `MILO_LIVE=1` (D126)."""
    if os.environ.get("MILO_LIVE") != "1":
        raise PermissionError(
            "a live embedding call spends OpenRouter budget (SPEC D126) and needs MILO_LIVE=1"
        )
    key = Settings.from_env().openrouter_api_key
    if not key:
        raise PermissionError("OPENROUTER_API_KEY is not set in the environment")
    return OpenRouterEmbedder(key, model)
