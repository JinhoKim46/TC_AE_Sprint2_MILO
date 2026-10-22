"""Pydantic loader for `data/pricing/rate_card.v1.yaml` (SPEC 5.2, D38).

The last volume tier may carry `factor: null` with a `note` (no automatic estimate). The return estimate also
needs `automated_minutes_per_request` and `eligible_share` per module; the data milestone adds them, so they are
optional here (SPEC 5.2: until then the report marks the return estimate `unavailable`).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

DEFAULT_PATH = Path("data/pricing/rate_card.v1.yaml")


class SetupFee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    per_module: float


class VolumeTier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    up_to: int | None
    factor: float | None
    note: str | None = None


class RangeFactors(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low_factor: float
    base_factor: float
    high_factor: float


class RateCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    currency: str
    base_monthly_usd: dict[str, float]
    setup_fee_usd: SetupFee
    integration_surcharge_monthly_usd: dict[str, float]
    volume_tiers_daily_requests: list[VolumeTier]
    range: RangeFactors
    max_modules_per_estimate: int
    notes: list[str] = []
    automated_minutes_per_request: dict[str, float] | None = None
    eligible_share: dict[str, float] | None = None

    @model_validator(mode="after")
    def _check(self) -> RateCard:
        tiers = self.volume_tiers_daily_requests
        if not tiers or tiers[-1].up_to is not None or tiers[-1].factor is not None:
            raise ValueError(
                "the last volume tier must have up_to null and factor null"
            )
        bounds = [t.up_to for t in tiers[:-1]]
        if any(b is None for b in bounds) or bounds != sorted(
            b for b in bounds if b is not None
        ):
            raise ValueError("volume tiers must have increasing up_to values")
        if any(t.factor is None for t in tiers[:-1]):
            raise ValueError("only the last volume tier may have a null factor")
        if (
            not self.range.low_factor
            <= self.range.base_factor
            <= self.range.high_factor
        ):
            raise ValueError("range factors must be ordered low <= base <= high")
        for table in (self.automated_minutes_per_request, self.eligible_share):
            if table is not None and set(table) != set(self.base_monthly_usd):
                raise ValueError(
                    "return-estimate tables must cover exactly the modules in the rate card"
                )
        return self


class RateCardFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate_card: RateCard


def load_rate_card(path: Path | str = DEFAULT_PATH) -> RateCard:
    with Path(path).open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return RateCardFile.model_validate(raw).rate_card
