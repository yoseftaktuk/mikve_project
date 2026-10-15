"""Runtime pricing catalog stored in access_service.pricing_config."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from .models import PricingConfig
from .settings import settings

PRICING_ROW_ID = 1
MAX_PRICE_CENTS = 1_000_000


@dataclass(frozen=True)
class Pricing:
    entrance_fee_cents: int
    subscription_price_cents: int


def _validate_price(cents: int, *, field: str) -> int:
    if cents <= 0 or cents > MAX_PRICE_CENTS:
        raise ValueError(f"invalid_{field}")
    return cents


async def ensure_pricing_row(db: AsyncSession) -> PricingConfig:
    """Insert the single pricing row from env defaults when missing (flush only)."""
    row = await db.get(PricingConfig, PRICING_ROW_ID)
    if row is not None:
        return row
    row = PricingConfig(
        id=PRICING_ROW_ID,
        entrance_fee_cents=settings.entrance_fee_cents,
        subscription_price_cents=settings.subscription_price_cents,
    )
    db.add(row)
    await db.flush()
    return row


async def get_pricing(db: AsyncSession) -> Pricing:
    """Return current prices, seeding from env if the row does not exist yet."""
    row = await ensure_pricing_row(db)
    return Pricing(
        entrance_fee_cents=row.entrance_fee_cents,
        subscription_price_cents=row.subscription_price_cents,
    )


async def get_entrance_fee_cents(db: AsyncSession) -> int:
    """Return the effective entrance fee in agorot."""
    return (await get_pricing(db)).entrance_fee_cents


async def update_pricing(
    db: AsyncSession,
    *,
    entrance_fee_cents: int | None = None,
    subscription_price_cents: int | None = None,
) -> Pricing:
    """Update one or both prices on the single pricing row."""
    if entrance_fee_cents is None and subscription_price_cents is None:
        raise ValueError("no_fields")
    row = await ensure_pricing_row(db)
    if entrance_fee_cents is not None:
        row.entrance_fee_cents = _validate_price(entrance_fee_cents, field="entrance_fee_cents")
    if subscription_price_cents is not None:
        row.subscription_price_cents = _validate_price(
            subscription_price_cents, field="subscription_price_cents"
        )
    await db.commit()
    return Pricing(
        entrance_fee_cents=row.entrance_fee_cents,
        subscription_price_cents=row.subscription_price_cents,
    )
