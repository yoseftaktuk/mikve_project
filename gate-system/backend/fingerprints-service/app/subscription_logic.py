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

from .hebrew_calendar import (
    daily_free_entry_limit,
    entry_day_kind,
    hebrew_date_for,
    hebrew_month_for,
    israel_date_from_timestamp,
    israel_now,
    israel_today,
)
from .models import STATUS_SUB_ACTIVE, Member, MemberActivity, MonthlySubscription

logger = logging.getLogger(__name__)

EVENT_FREE_ENTRY = "subscription_free_entry"


@dataclass(frozen=True)
class SubscriptionSnapshot:
    """Subscription fields returned on validate / activate responses."""

    subscription_active: bool
    subscription_month_name: str | None
    subscription_hebrew_year: int | None
    subscription_hebrew_month: int | None
    subscription_free_entry_available_today: bool
    current_hebrew_month_name: str


def _free_entry_activity_key(member_id: uuid.UUID, today: date, token: str) -> str:
    """Unique member_activity key. Caller token (attempt id) is enough to stay under 80 chars."""
    if token:
        return f"sub-free:{token}"
    return f"sub-free:{member_id}:{today.isoformat()}"


async def get_active_subscription(
    db: AsyncSession,
    member_id: uuid.UUID,
    *,
    day: date | None = None,
    now: datetime | None = None,
    for_update: bool = False,
) -> MonthlySubscription | None:
    """Return the active subscription for the current Hebrew month, if any."""
    month = hebrew_month_for(day, now=now)
    stmt = select(MonthlySubscription).where(
        MonthlySubscription.member_id == member_id,
        MonthlySubscription.hebrew_year == month.year,
        MonthlySubscription.hebrew_month == month.month,
        MonthlySubscription.status == STATUS_SUB_ACTIVE,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return await db.scalar(stmt)


async def _activity_by_key(db: AsyncSession, key: str) -> MemberActivity | None:
    return await db.scalar(select(MemberActivity).where(MemberActivity.idempotency_key == key))


async def count_today_free_entries(
    db: AsyncSession,
    *,
    member_id: uuid.UUID,
    today: date,
    sub: MonthlySubscription,
) -> int:
    """Successful free entries whose timestamps fall on this Israel civil date."""
    rows = (
        await db.scalars(
            select(MemberActivity).where(
                MemberActivity.member_id == member_id,
                MemberActivity.event_type == EVENT_FREE_ENTRY,
            )
        )
    ).all()
    count = 0
    for row in rows:
        if row.created_at is None:
            continue
        if israel_date_from_timestamp(row.created_at) == today:
            count += 1
    if count == 0 and sub.last_free_entry_on == today:
        return 1
    return count


def _log_free_entry_decision(
    *,
    member_id: uuid.UUID,
    active: bool,
    now: datetime,
    used: int,
    limit: int,
    available: bool,
    action: str,
) -> None:
    local = israel_now(now)
    hd = hebrew_date_for(local.date())
    logger.info(
        "subscription_free_entry member_id=%s action=%s active=%s israel_now=%s "
        "hebrew=%s/%s/%s kind=%s limit=%s used=%s available=%s",
        member_id,
        action,
        active,
        local.isoformat(),
        hd.year,
        hd.month,
        hd.day,
        entry_day_kind(local),
        limit,
        used,
        available,
    )


async def subscription_snapshot(
    db: AsyncSession,
    member_id: uuid.UUID,
    *,
    day: date | None = None,
    now: datetime | None = None,
) -> SubscriptionSnapshot:
    """Build subscription flags for API responses."""
    local = israel_now(now)
    today = day or local.date()
    month = hebrew_month_for(today)
    sub = await get_active_subscription(db, member_id, day=today)
    limit = daily_free_entry_limit(local)
    used = 0
    free_available = False
    if sub is not None:
        used = await count_today_free_entries(db, member_id=member_id, today=today, sub=sub)
        free_available = used < limit
    _log_free_entry_decision(
        member_id=member_id,
        active=sub is not None,
        now=local,
        used=used,
        limit=limit,
        available=free_available,
        action="snapshot",
    )
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
    member_id: uuid.UUID,
    amount_cents: int,
    nedarim_transaction_id: str,
    hebrew_year: int | None = None,
    hebrew_month: int | None = None,
    hebrew_month_name: str | None = None,
) -> MonthlySubscription:
    """Create or return an active subscription for the given/current Hebrew month.

    Idempotent on nedarim_transaction_id. Rejects a second purchase for the same
    member+month with a different transaction id.
    """
    member = await db.get(Member, member_id)
    if member is None:
        raise AppError(code="member_not_found", message="Member not found", http_status=404)

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
            MonthlySubscription.member_id == member_id,
            MonthlySubscription.hebrew_year == year,
            MonthlySubscription.hebrew_month == month_num,
            MonthlySubscription.status == STATUS_SUB_ACTIVE,
        )
    )
    if existing_month is not None:
        raise AppError(
            code="subscription_already_active",
            message="Member already has an active subscription for this Hebrew month",
            http_status=409,
        )

    row = MonthlySubscription(
        member_id=member_id,
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
        MemberActivity(
            member_id=member_id,
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
            message="Member already has an active subscription for this Hebrew month",
            http_status=409,
        ) from None
    await db.refresh(row)
    logger.info(
        "subscription_activated member_id=%s year=%s month=%s txn=%s",
        member_id,
        year,
        month_num,
        txn,
    )
    return row


async def mark_free_entry(
    db: AsyncSession,
    *,
    member_id: uuid.UUID,
    day: date | None = None,
    now: datetime | None = None,
    idempotency_key: str | None = None,
) -> MonthlySubscription:
    """Record one free subscription entrance if a slot remains for this Israel day."""
    local = israel_now(now)
    today = day or local.date()
    token = (idempotency_key or "").strip()
    activity_key = _free_entry_activity_key(member_id, today, token)

    if token:
        existing = await _activity_by_key(db, activity_key)
        if existing is not None:
            sub = await get_active_subscription(db, member_id, day=today)
            if sub is None:
                raise AppError(
                    code="subscription_inactive",
                    message="No active subscription for the current Hebrew month",
                    http_status=409,
                )
            return sub

    sub = await get_active_subscription(db, member_id, day=today, for_update=True)
    if sub is None:
        await db.rollback()
        raise AppError(
            code="subscription_inactive",
            message="No active subscription for the current Hebrew month",
            http_status=409,
        )

    if token:
        existing = await _activity_by_key(db, activity_key)
        if existing is not None:
            await db.rollback()
            return sub

    limit = daily_free_entry_limit(local)
    used = await count_today_free_entries(db, member_id=member_id, today=today, sub=sub)
    if used >= limit:
        await db.rollback()
        _log_free_entry_decision(
            member_id=member_id,
            active=True,
            now=local,
            used=used,
            limit=limit,
            available=False,
            action="mark_denied",
        )
        raise AppError(
            code="daily_limit_reached",
            message="No free subscription entries remain for today",
            http_status=409,
        )

    if not token:
        activity_key = f"sub-free:{member_id}:{today.isoformat()}:{used + 1}"

    sub.last_free_entry_on = today
    db.add(
        MemberActivity(
            member_id=member_id,
            event_type=EVENT_FREE_ENTRY,
            delta_cents=0,
            description=f"free entry on {today.isoformat()}",
            idempotency_key=activity_key,
            created_at=datetime.now(timezone.utc) if now is None else israel_now(now).astimezone(timezone.utc),
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raced = await _activity_by_key(db, activity_key)
        if raced is not None:
            refreshed = await get_active_subscription(db, member_id, day=today)
            if refreshed is not None:
                return refreshed
        refreshed = await get_active_subscription(db, member_id, day=today)
        if refreshed is None:
            raise AppError(
                code="subscription_inactive",
                message="No active subscription for the current Hebrew month",
                http_status=409,
            ) from None
        used_after = await count_today_free_entries(db, member_id=member_id, today=today, sub=refreshed)
        if used_after >= daily_free_entry_limit(local):
            raise AppError(
                code="daily_limit_reached",
                message="No free subscription entries remain for today",
                http_status=409,
            ) from None
        return refreshed
    await db.refresh(sub)
    _log_free_entry_decision(
        member_id=member_id,
        active=True,
        now=local,
        used=used + 1,
        limit=limit,
        available=used + 1 < limit,
        action="mark",
    )
    return sub
