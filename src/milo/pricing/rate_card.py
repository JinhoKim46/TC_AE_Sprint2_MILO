"""Rate-card access (SPEC 5.2, D38). Constants come from `data/pricing/rate_card.v1.yaml` and nowhere else."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

from milo.pricing.schema import RateCard, load_rate_card

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CARD_PATH = REPO_ROOT / "data" / "pricing" / "rate_card.v1.yaml"


@lru_cache(maxsize=8)
def _load(path: str) -> RateCard:
    return load_rate_card(path)


def get_rate_card(path: Path | str | None = None) -> RateCard:
    """The validated rate card (cached by path). The default is the committed v1 file."""
    return _load(str(path if path is not None else DEFAULT_CARD_PATH))


def dec(value: float | int | str) -> Decimal:
    """A rate-card float as an exact decimal (`1.25` stays 1.25)."""
    return Decimal(str(value))


def money(value: Decimal) -> float:
    """Round to cents, half up. Every figure shown to the owner goes through this."""
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def volume_factor(card: RateCard, daily_requests: float | Decimal) -> float | None:
    """The tier factor for a daily volume (`up_to` is inclusive). None above the last bounded tier: no estimate."""
    volume = (
        dec(daily_requests)
        if not isinstance(daily_requests, Decimal)
        else daily_requests
    )
    if volume <= 0:
        raise ValueError("daily volume must be positive")
    for tier in card.volume_tiers_daily_requests:
        if tier.up_to is None:
            return None
        if volume <= dec(tier.up_to):
            return tier.factor
    return None


def known_modules(card: RateCard) -> list[str]:
    return list(card.base_monthly_usd)


def known_integrations(card: RateCard) -> list[str]:
    return list(card.integration_surcharge_monthly_usd)
