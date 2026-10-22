"""Embedders (SPEC D5, D126). `OpenRouterEmbedder` goes through `milo.llm.cloud`; `FakeEmbedder` is for tests,
the dry harness and replay: deterministic, no model, no network.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import time
from collections.abc import Callable
from functools import partial
from typing import Any

from milo.llm import cloud
from milo.llm.retry import call_with_retry
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
    """Batches texts and calls `cloud.OpenRouterEmbeddings`. Each batch goes through the one retry site
    (`llm/retry.py`, at most 3 attempts); a final failure is a typed `ProviderFailure`.

    `total_tokens` and `total_cost_usd` add up the usage of the successful batches (see `cloud`).
    """

    def __init__(
        self,
        api_key: str,
        model: str = cloud.DEFAULT_EMBEDDING_MODEL,
        *,
        batch: int = BATCH,
        client: Any = None,
        price_usd_per_mtok: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self.batch = batch
        self.calls = 0
        self._sleep = sleep
        self._inner = cloud.OpenRouterEmbeddings(
            api_key, model, client=client, price_usd_per_mtok=price_usd_per_mtok
        )

    @property
    def total_tokens(self) -> int:
        return self._inner.total_tokens

    @property
    def total_cost_usd(self) -> float:
        return self._inner.total_cost_usd

    @property
    def cost_unknown_calls(self) -> int:
        return self._inner.cost_unknown_calls

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            chunk = texts[i : i + self.batch]
            out += call_with_retry(partial(self._inner.embed, chunk), sleep=self._sleep)
            self.calls += 1
        return out


def live_embedder(
    model: str = cloud.DEFAULT_EMBEDDING_MODEL,
    price_usd_per_mtok: float | None = None,
) -> OpenRouterEmbedder:
    """The OpenRouter embedder from the process environment. Refuses without `MILO_LIVE=1` (D126)."""
    if os.environ.get("MILO_LIVE") != "1":
        raise PermissionError(
            "a live embedding call spends OpenRouter budget (SPEC D126) and needs MILO_LIVE=1"
        )
    key = Settings.from_env().openrouter_api_key
    if not key:
        raise PermissionError("OPENROUTER_API_KEY is not set in the environment")
    return OpenRouterEmbedder(key, model, price_usd_per_mtok=price_usd_per_mtok)
