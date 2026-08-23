"""Aggregated management statistics across access, fingerprints, and payment schemas."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

STATISTICS_TIMEZONE = "Asia/Jerusalem"
_JERUSALEM = ZoneInfo(STATISTICS_TIMEZONE)

StatisticsRangeKey = Literal["today", "7d", "month"]
VALID_RANGES: frozenset[str] = frozenset({"today", "7d", "month"})


class StatisticsRange(BaseModel):
    """Inclusive start / exclusive end window in Asia/Jerusalem, as UTC-aware datetimes."""

    range: StatisticsRangeKey
    timezone: str = STATISTICS_TIMEZONE
    start: datetime
    end: datetime


class StatisticsKpis(BaseModel):
    granted_count: int = 0
    denied_count: int = 0
    approval_rate: float = 0.0
    entrance_fee_total_cents: int = 0
    paid_card_topup_count: int = 0
    paid_card_topup_total_cents: int = 0
    paid_monthly_subscription_count: int = 0
    paid_monthly_subscription_total_cents: int = 0
    enabled_member_count: int = 0
    disabled_member_count: int = 0
    total_member_balances_cents: int = 0


class DailyAccessPoint(BaseModel):
    date: str
    granted_count: int = 0
    denied_count: int = 0
    entrance_fee_total_cents: int = 0


class DenialReasonCount(BaseModel):
    reason: str
    count: int


class AccessMethodsBreakdown(BaseModel):
    cash: int = 0
    member: int = 0
    subscription: int = 0


class OperationalSummary(BaseModel):
    pending_card_topups: int = 0
    crediting_card_topups: int = 0
    failed_access_attempts: int = 0
    door_operation_failures: int = 0


class ManagementStatisticsResponse(BaseModel):
    range: StatisticsRangeKey
    timezone: str = STATISTICS_TIMEZONE
    from_: datetime = Field(validation_alias="from", serialization_alias="from")
    to: datetime
    kpis: StatisticsKpis
    daily_access: list[DailyAccessPoint]
    denial_reasons: list[DenialReasonCount]
    access_methods: AccessMethodsBreakdown
    operational: OperationalSummary

    model_config = {"populate_by_name": True}


def approval_rate(granted: int, denied: int) -> float:
    """Return granted / (granted + denied), or 0 when there are no decisions."""
    total = granted + denied
    if total <= 0:
        return 0.0
    return granted / total


def resolve_statistics_range(
    range_key: str,
    *,
    now: datetime | None = None,
) -> StatisticsRange:
    """Resolve a preset range key into [start, end) bounds in Asia/Jerusalem."""
    if range_key not in VALID_RANGES:
        raise ValueError("invalid_range")

    current = now.astimezone(_JERUSALEM) if now is not None else datetime.now(_JERUSALEM)
    today: date = current.date()
    start_of_today = datetime.combine(today, time.min, tzinfo=_JERUSALEM)
    end = start_of_today + timedelta(days=1)

    if range_key == "today":
        start = start_of_today
    elif range_key == "7d":
        start = start_of_today - timedelta(days=6)
    else:  # month
        start = datetime.combine(today.replace(day=1), time.min, tzinfo=_JERUSALEM)

    return StatisticsRange(range=range_key, start=start, end=end)  # type: ignore[arg-type]


def _as_int(value: object) -> int:
    if value is None:
        return 0
    return int(value)


async def fetch_management_statistics(
    db: AsyncSession,
    range_key: str,
    *,
    now: datetime | None = None,
) -> ManagementStatisticsResponse:
    """Run bounded cross-schema aggregates for the management statistics panel."""
    window = resolve_statistics_range(range_key, now=now)
    start = window.start
    end = window.end

    access_row = (
        await db.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE decision = 'granted') AS granted_count,
                  COUNT(*) FILTER (WHERE decision = 'denied') AS denied_count,
                  COALESCE(
                    SUM(fee_cents) FILTER (WHERE decision = 'granted'),
                    0
                  ) AS entrance_fee_total_cents
                FROM access_service.access_logs
                WHERE created_at >= :start AND created_at < :end
                """
            ),
            {"start": start, "end": end},
        )
    ).one()

    granted_count = _as_int(access_row.granted_count)
    denied_count = _as_int(access_row.denied_count)
    entrance_fee_total_cents = _as_int(access_row.entrance_fee_total_cents)

    daily_rows = (
        await db.execute(
            text(
                """
                SELECT
                  (created_at AT TIME ZONE 'Asia/Jerusalem')::date AS day,
                  COUNT(*) FILTER (WHERE decision = 'granted') AS granted_count,
                  COUNT(*) FILTER (WHERE decision = 'denied') AS denied_count,
                  COALESCE(
                    SUM(fee_cents) FILTER (WHERE decision = 'granted'),
                    0
                  ) AS entrance_fee_total_cents
                FROM access_service.access_logs
                WHERE created_at >= :start AND created_at < :end
                GROUP BY day
                ORDER BY day
                """
            ),
            {"start": start, "end": end},
        )
    ).all()

    denial_rows = (
        await db.execute(
            text(
                """
                SELECT reason, COUNT(*) AS count
                FROM access_service.access_logs
                WHERE decision = 'denied'
                  AND created_at >= :start
                  AND created_at < :end
                GROUP BY reason
                ORDER BY count DESC, reason ASC
                """
            ),
            {"start": start, "end": end},
        )
    ).all()

    method_rows = (
        await db.execute(
            text(
                """
                SELECT pt.provider, COUNT(*) AS count
                FROM access_service.access_attempts aa
                JOIN access_service.payment_transactions pt
                  ON pt.attempt_id = aa.id
                WHERE aa.status = 'COMPLETED'
                  AND pt.status = 'succeeded'
                  AND aa.created_at >= :start
                  AND aa.created_at < :end
                GROUP BY pt.provider
                """
            ),
            {"start": start, "end": end},
        )
    ).all()

    paid_topup_row = (
        await db.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE product = 'balance') AS paid_card_topup_count,
                  COALESCE(
                    SUM(amount_cents) FILTER (WHERE product = 'balance'),
                    0
                  ) AS paid_card_topup_total_cents,
                  COUNT(*) FILTER (WHERE product = 'monthly_subscription') AS paid_monthly_subscription_count,
                  COALESCE(
                    SUM(amount_cents) FILTER (WHERE product = 'monthly_subscription'),
                    0
                  ) AS paid_monthly_subscription_total_cents
                FROM payment_service.card_topups
                WHERE status = 'paid'
                  AND credited_at IS NOT NULL
                  AND credited_at >= :start
                  AND credited_at < :end
                """
            ),
            {"start": start, "end": end},
        )
    ).one()

    open_topup_row = (
        await db.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE status = 'pending') AS pending_card_topups,
                  COUNT(*) FILTER (WHERE status = 'crediting') AS crediting_card_topups
                FROM payment_service.card_topups
                WHERE status IN ('pending', 'crediting')
                """
            )
        )
    ).one()

    members_row = (
        await db.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE is_enabled IS TRUE) AS enabled_member_count,
                  COUNT(*) FILTER (WHERE is_enabled IS FALSE) AS disabled_member_count
                FROM fingerprints_service.members
                """
            )
        )
    ).one()

    balances_row = (
        await db.execute(
            text(
                """
                SELECT COALESCE(SUM(amount_cents), 0) AS total_member_balances_cents
                FROM fingerprints_service.balances
                """
            )
        )
    ).one()

    failed_attempts_row = (
        await db.execute(
            text(
                """
                SELECT COUNT(*) AS failed_access_attempts
                FROM access_service.access_attempts
                WHERE status IN ('FAILED', 'MANUAL_REVIEW')
                """
            )
        )
    ).one()

    door_failures_row = (
        await db.execute(
            text(
                """
                SELECT COUNT(*) AS door_operation_failures
                FROM access_service.door_operations
                WHERE status IN ('timeout', 'error')
                  AND started_at >= :start
                  AND started_at < :end
                """
            ),
            {"start": start, "end": end},
        )
    ).one()

    methods = AccessMethodsBreakdown()
    for row in method_rows:
        provider = str(row.provider or "")
        count = _as_int(row.count)
        if provider == "cash":
            methods.cash = count
        elif provider == "member":
            methods.member = count
        elif provider == "subscription":
            methods.subscription = count

    return ManagementStatisticsResponse(
        range=window.range,
        timezone=window.timezone,
        from_=window.start,
        to=window.end,
        kpis=StatisticsKpis(
            granted_count=granted_count,
            denied_count=denied_count,
            approval_rate=approval_rate(granted_count, denied_count),
            entrance_fee_total_cents=entrance_fee_total_cents,
            paid_card_topup_count=_as_int(paid_topup_row.paid_card_topup_count),
            paid_card_topup_total_cents=_as_int(paid_topup_row.paid_card_topup_total_cents),
            paid_monthly_subscription_count=_as_int(paid_topup_row.paid_monthly_subscription_count),
            paid_monthly_subscription_total_cents=_as_int(
                paid_topup_row.paid_monthly_subscription_total_cents
            ),
            enabled_member_count=_as_int(members_row.enabled_member_count),
            disabled_member_count=_as_int(members_row.disabled_member_count),
            total_member_balances_cents=_as_int(balances_row.total_member_balances_cents),
        ),
        daily_access=[
            DailyAccessPoint(
                date=row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day),
                granted_count=_as_int(row.granted_count),
                denied_count=_as_int(row.denied_count),
                entrance_fee_total_cents=_as_int(row.entrance_fee_total_cents),
            )
            for row in daily_rows
        ],
        denial_reasons=[
            DenialReasonCount(reason=str(row.reason), count=_as_int(row.count)) for row in denial_rows
        ],
        access_methods=methods,
        operational=OperationalSummary(
            pending_card_topups=_as_int(open_topup_row.pending_card_topups),
            crediting_card_topups=_as_int(open_topup_row.crediting_card_topups),
            failed_access_attempts=_as_int(failed_attempts_row.failed_access_attempts),
            door_operation_failures=_as_int(door_failures_row.door_operation_failures),
        ),
    )
