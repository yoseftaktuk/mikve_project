"""Read subscription price from access_service.pricing_config (env fallback)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .settings import settings

# Owned by access-control-service; payment only reads the catalog value.
_ACCESS_PRICING_SQL = text(
    """
    SELECT subscription_price_cents
    FROM access_service.pricing_config
    WHERE id = 1
    """
)


async def get_subscription_price_cents(db: AsyncSession) -> int:
    """Return the effective monthly subscription price in agorot.

    Prefers the shared access_service.pricing_config row; falls back to
    SUBSCRIPTION_PRICE_CENTS when the table/row is not yet available.
    """
    try:
        value = await db.scalar(_ACCESS_PRICING_SQL)
    except Exception:
        return settings.subscription_price_cents
    if value is None:
        return settings.subscription_price_cents
    return int(value)
