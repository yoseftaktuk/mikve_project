"""Monthly Hebrew-month subscription activate / free-entry helpers."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gate_shared.errors import AppError

from .hebrew_calendar import hebrew_month_for, israel_today
from .models import STATUS_SUB_ACTIVE, Chip, ChipActivity, MonthlySubscription

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubscriptionSnapshot:
    """Subscription fields returned on validate / activate responses."""

    subscription_active: bool
    subscription_month_name: str | None
    subscription_hebrew_year: int | None
    subscription_hebrew_month: int | None
    subscription_free_entry_available_today: bool
    current_hebrew_month_name: str


async def get_active_subscription(
    db: AsyncSession, chip_id: uuid.UUID, *, day: date | None = None
) -> MonthlySubscription | None:
    """Return the active subscription for the current Hebrew month, if any."""
    month = hebrew_month_for(day)
    return await db.scalar(
        select(MonthlySubscription).where(
            MonthlySubscription.chip_id == chip_id,
            MonthlySubscription.hebrew_year == month.year,
            MonthlySubscription.hebrew_month == month.month,
            MonthlySubscription.status == STATUS_SUB_ACTIVE,
        )
    )


async def subscription_snapshot(
    db: AsyncSession, chip_id: uuid.UUID, *, day: date | None = None
) -> SubscriptionSnapshot:
    """Build subscription flags for API responses."""
    today = day or israel_today()
    month = hebrew_month_for(today)
    sub = await get_active_subscription(db, chip_id, day=today)
    free_available = False
    if sub is not None:
        free_available = sub.last_free_entry_on != today
    return SubscriptionSnapshot(
        subscription_active=sub is not None,
        subscription_month_name=sub.hebrew_month_name if sub else None,
        subscription_hebrew_year=sub.hebrew_year if sub else None,
        subscription_hebrew_month=sub.hebrew_month if sub else None,
        subscription_free_entry_available_today=free_available,
        current_hebrew_month_name=month.name,
    )


async def activate_subscription(
    db: AsyncSession,
    *,
    chip_id: uuid.UUID,
    amount_cents: int,
    nedarim_transaction_id: str,
    hebrew_year: int | None = None,
    hebrew_month: int | None = None,
    hebrew_month_name: str | None = None,
) -> MonthlySubscription:
    """Create or return an active subscription for the given/current Hebrew month.

    Idempotent on nedarim_transaction_id. Rejects a second purchase for the same
    chip+month with a different transaction id.
    """
    chip = await db.get(Chip, chip_id)
    if chip is None:
        raise AppError(code="chip_not_found", message="Chip not found", http_status=404)

    txn = nedarim_transaction_id.strip()
    if not txn:
        raise AppError(code="bad_transaction_id", message="nedarim_transaction_id is required", http_status=400)

    existing_txn = await db.scalar(
        select(MonthlySubscription).where(MonthlySubscription.nedarim_transaction_id == txn)
    )
    if existing_txn is not None:
        return existing_txn

    month = hebrew_month_for()
    year = month.year if hebrew_year is None else hebrew_year
    month_num = month.month if hebrew_month is None else hebrew_month
    name = hebrew_month_name or month.name
    if year == month.year and month_num == month.month:
        name = month.name

    existing_month = await db.scalar(
        select(MonthlySubscription).where(
            MonthlySubscription.chip_id == chip_id,
            MonthlySubscription.hebrew_year == year,
            MonthlySubscription.hebrew_month == month_num,
            MonthlySubscription.status == STATUS_SUB_ACTIVE,
        )
    )
    if existing_month is not None:
        raise AppError(
            code="subscription_already_active",
            message="Chip already has an active subscription for this Hebrew month",
            http_status=409,
        )

    row = MonthlySubscription(
        chip_id=chip_id,
        hebrew_year=year,
        hebrew_month=month_num,
        hebrew_month_name=name,
        amount_cents=amount_cents,
        nedarim_transaction_id=txn,
        status=STATUS_SUB_ACTIVE,
        purchased_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.add(
        ChipActivity(
            chip_id=chip_id,
            event_type="subscription_activate",
            delta_cents=0,
            description=f"monthly subscription {year}-{month_num} ({name})",
            idempotency_key=f"sub-activate:{txn}",
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raced = await db.scalar(
            select(MonthlySubscription).where(MonthlySubscription.nedarim_transaction_id == txn)
        )
        if raced is not None:
            return raced
        raise AppError(
            code="subscription_already_active",
            message="Chip already has an active subscription for this Hebrew month",
            http_status=409,
        ) from None
    await db.refresh(row)
    logger.info(
        "subscription_activated chip_id=%s year=%s month=%s txn=%s",
        chip_id,
        year,
        month_num,
        txn,
    )
    return row


async def mark_free_entry(
    db: AsyncSession,
    *,
    chip_id: uuid.UUID,
    day: date | None = None,
) -> MonthlySubscription:
    """Record today's free subscription entrance (idempotent for the same day)."""
    today = day or israel_today()
    sub = await get_active_subscription(db, chip_id, day=today)
    if sub is None:
        raise AppError(
            code="subscription_inactive",
            message="No active subscription for the current Hebrew month",
            http_status=409,
        )
    if sub.last_free_entry_on == today:
        return sub
    sub.last_free_entry_on = today
    db.add(
        ChipActivity(
            chip_id=chip_id,
            event_type="subscription_free_entry",
            delta_cents=0,
            description=f"free entry on {today.isoformat()}",
            idempotency_key=f"sub-free:{chip_id}:{today.isoformat()}",
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        refreshed = await get_active_subscription(db, chip_id, day=today)
        if refreshed is not None:
            return refreshed
        raise
    await db.refresh(sub)
    return sub
