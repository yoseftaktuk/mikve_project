from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .clients import FingerprintsClient
from .models import (
    CURRENCY_ILS,
    WEBHOOK_DUPLICATE,
    WEBHOOK_FAILED,
    WEBHOOK_IGNORED_CATEGORY,
    WEBHOOK_INVALID,
    WEBHOOK_PROCESSED,
    WEBHOOK_RECEIVED,
    WEBHOOK_USER_UNRESOLVED,
    CardTopup,
    NedarimWebhookEvent,
)
from .nedarim_plus.callback import NEDARIM_CALLBACK_IPS, assert_callback_source_ip
from .nedarim_plus.errors import NedarimError
from .hebrew_calendar import current_hebrew_month
from .nedarim_plus.webhook import parse_webhook_fields, redact_webhook_payload
from .settings import settings

logger = logging.getLogger(__name__)

# ignored_category is not terminal: the first delivery may have used an
# outdated Groupe allow-list, and nothing was credited, so a retry is safe.
TERMINAL_NO_RETRY = frozenset(
    {
        WEBHOOK_PROCESSED,
        WEBHOOK_USER_UNRESOLVED,
        WEBHOOK_INVALID,
        WEBHOOK_DUPLICATE,
    }
)

_STATUS_HTTP: dict[str, tuple[bool, int, str]] = {
    WEBHOOK_PROCESSED: (True, 200, "Webhook processed"),
    WEBHOOK_IGNORED_CATEGORY: (True, 200, "Groupe is not the target category"),
    WEBHOOK_USER_UNRESOLVED: (True, 200, "Zeout did not resolve to exactly one chip"),
    WEBHOOK_DUPLICATE: (True, 200, "Transaction already processed"),
    WEBHOOK_INVALID: (False, 400, "Webhook payload is invalid"),
    WEBHOOK_FAILED: (False, 500, "Webhook processing failed"),
}


@dataclass(frozen=True)
class WebhookHandlingResult:
    """Outcome of an institution webhook, for the HTTP layer."""

    accepted: bool
    http_status: int
    code: str
    message: str


def webhook_allows_local_bypass() -> bool:
    """Dev-only skip of IP/CF checks. Never honoured in production."""
    if settings.environment.lower() == "production":
        return False
    return bool(settings.nedarim_webhook_allow_local)


def _result_for_status(status: str, message: str | None = None) -> WebhookHandlingResult:
    accepted, http_status, default_message = _STATUS_HTTP.get(
        status, (False, 500, "Webhook processing failed")
    )
    return WebhookHandlingResult(
        accepted=accepted,
        http_status=http_status,
        code=status,
        message=message or default_message,
    )


def _zeout_log_token(zeout: str | None) -> str:
    if not zeout:
        return "-"
    return zeout[-4:]


async def _settle(
    db: AsyncSession,
    event: NedarimWebhookEvent,
    status: str,
    *,
    error: str | None = None,
    matched_chip_id: uuid.UUID | None = None,
) -> NedarimWebhookEvent:
    event.processing_status = status
    event.processing_error = error
    event.processed_at = datetime.now(timezone.utc)
    if matched_chip_id is not None:
        event.matched_chip_id = matched_chip_id
    await db.commit()
    return event


def _log_outcome(
    *,
    transaction_id: str,
    groupe: str | None,
    amount_cents: int | None,
    currency: int | None,
    zeout: str | None,
    matched_chip_id: uuid.UUID | None,
    status: str,
    elapsed_ms: float,
    error: str | None = None,
) -> None:
    logger.info(
        "nedarim_webhook transaction_id=%s groupe=%r amount_cents=%s currency=%s "
        "zeout_suffix=%s matched_chip_id=%s status=%s duration_ms=%.1f error=%s",
        transaction_id,
        groupe,
        amount_cents,
        currency,
        _zeout_log_token(zeout),
        matched_chip_id,
        status,
        elapsed_ms,
        error or "-",
    )


async def _load_event(db: AsyncSession, transaction_id: str) -> NedarimWebhookEvent | None:
    return await db.scalar(
        select(NedarimWebhookEvent).where(NedarimWebhookEvent.transaction_id == transaction_id)
    )


async def _kiosk_already_credited(db: AsyncSession, transaction_id: str) -> bool:
    row = await db.scalar(
        select(CardTopup).where(CardTopup.nedarim_transaction_id == transaction_id)
    )
    return row is not None


async def process_nedarim_webhook(
    *,
    payload: dict[str, Any],
    source_ip: str | None,
    db: AsyncSession,
    chip_client: FingerprintsClient,
    skip_source_ip: bool = False,
) -> WebhookHandlingResult:
    """Record an institution webhook; activate a subscription or credit balance by Groupe."""
    started = time.perf_counter()

    if not skip_source_ip:
        try:
            assert_callback_source_ip(source_ip)
        except NedarimError:
            logger.warning(
                "nedarim_webhook_rejected_ip source_ip=%s allowed=%s",
                source_ip,
                ",".join(sorted(NEDARIM_CALLBACK_IPS)),
            )
            return WebhookHandlingResult(
                accepted=False,
                http_status=403,
                code="bad_ip",
                message=f"Callback source IP is not allowed: {source_ip!r}",
            )

    try:
        fields = parse_webhook_fields(payload)
    except NedarimError as exc:
        return WebhookHandlingResult(
            accepted=False,
            http_status=400,
            code="invalid",
            message=exc.message,
        )

    if not fields.transaction_id:
        return _result_for_status(WEBHOOK_INVALID, "Webhook is missing TransactionId")

    event = NedarimWebhookEvent(
        transaction_id=fields.transaction_id,
        zeout=fields.zeout_normalized,
        groupe=fields.groupe,
        amount_cents=fields.amount_cents,
        currency=fields.currency,
        transaction_time=fields.transaction_time,
        confirmation=fields.confirmation,
        transaction_type=fields.transaction_type,
        processing_status=WEBHOOK_RECEIVED,
        raw_payload=redact_webhook_payload(payload),
    )
    db.add(event)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await _load_event(db, fields.transaction_id)
        if existing is None:
            return _result_for_status(WEBHOOK_FAILED, "Duplicate claim lost the original row")
        if existing.processing_status in TERMINAL_NO_RETRY:
            elapsed = (time.perf_counter() - started) * 1000
            _log_outcome(
                transaction_id=fields.transaction_id,
                groupe=existing.groupe,
                amount_cents=existing.amount_cents,
                currency=existing.currency,
                zeout=existing.zeout,
                matched_chip_id=existing.matched_chip_id,
                status=WEBHOOK_DUPLICATE if existing.processing_status == WEBHOOK_PROCESSED else existing.processing_status,
                elapsed_ms=elapsed,
            )
            if existing.processing_status == WEBHOOK_PROCESSED:
                return _result_for_status(WEBHOOK_DUPLICATE)
            return _result_for_status(existing.processing_status)
        event = existing

    async def finish(status: str, *, error: str | None = None, chip_id: uuid.UUID | None = None) -> WebhookHandlingResult:
        await _settle(db, event, status, error=error, matched_chip_id=chip_id)
        elapsed = (time.perf_counter() - started) * 1000
        _log_outcome(
            transaction_id=fields.transaction_id,
            groupe=fields.groupe,
            amount_cents=fields.amount_cents,
            currency=fields.currency,
            zeout=fields.zeout_normalized,
            matched_chip_id=chip_id or event.matched_chip_id,
            status=status,
            elapsed_ms=elapsed,
            error=error,
        )
        return _result_for_status(status, error)

    is_subscription = fields.groupe == settings.nedarim_target_group
    is_balance = fields.groupe in settings.nedarim_balance_groups
    if fields.groupe in settings.nedarim_not_allowed_groups:
        return await finish(WEBHOOK_IGNORED_CATEGORY, error="not_allowed_groupe")
    if not is_subscription and not is_balance:
        return await finish(WEBHOOK_IGNORED_CATEGORY, error="groupe_mismatch")

    if not fields.zeout_normalized:
        reason = "missing_zeout" if not fields.zeout_raw else "malformed_zeout"
        return await finish(WEBHOOK_USER_UNRESOLVED, error=reason)

    if fields.amount_error or fields.amount_cents is None:
        return await finish(WEBHOOK_INVALID, error=fields.amount_error or "missing_amount")

    if fields.currency_error:
        return await finish(WEBHOOK_INVALID, error=fields.currency_error)
    if fields.currency != CURRENCY_ILS:
        return await finish(WEBHOOK_INVALID, error="unsupported_currency")

    try:
        match = await chip_client.lookup_by_national_id(fields.zeout_normalized)
    except ValueError as exc:
        code = str(exc) or "chip_not_found"
        reason = "ambiguous_zeout" if code == "national_id_ambiguous" else "unknown_zeout"
        return await finish(WEBHOOK_USER_UNRESOLVED, error=reason)

    if not match.is_enabled:
        logger.warning(
            "nedarim_webhook_disabled_chip transaction_id=%s chip_id=%s zeout_suffix=%s",
            fields.transaction_id,
            match.chip_id,
            _zeout_log_token(fields.zeout_normalized),
        )

    if await _kiosk_already_credited(db, fields.transaction_id):
        return await finish(
            WEBHOOK_DUPLICATE,
            error="kiosk_already_credited",
            chip_id=uuid.UUID(match.chip_id),
        )

    if is_balance:
        try:
            await chip_client.adjust_balance(
                chip_id=match.chip_id,
                delta_cents=fields.amount_cents,
                reason="nedarim_webhook",
                description=f"Nedarim Plus webhook {fields.transaction_id}",
                idempotency_key=f"nedarim:{fields.transaction_id}",
            )
        except Exception:
            logger.exception(
                "nedarim_webhook_credit_failed transaction_id=%s chip_id=%s",
                fields.transaction_id,
                match.chip_id,
            )
            return await finish(WEBHOOK_FAILED, error="credit_failed", chip_id=uuid.UUID(match.chip_id))
        return await finish(WEBHOOK_PROCESSED, chip_id=uuid.UUID(match.chip_id))

    hebrew = current_hebrew_month()
    try:
        await chip_client.activate_subscription(
            match.chip_id,
            amount_cents=fields.amount_cents,
            nedarim_transaction_id=fields.transaction_id,
            hebrew_year=hebrew.year,
            hebrew_month=hebrew.month,
            hebrew_month_name=hebrew.name,
        )
    except ValueError as exc:
        code = str(exc) or "subscription_activate_failed"
        if code == "subscription_already_active":
            return await finish(
                WEBHOOK_FAILED,
                error="subscription_already_active",
                chip_id=uuid.UUID(match.chip_id),
            )
        logger.exception(
            "nedarim_webhook_subscription_failed transaction_id=%s chip_id=%s code=%s",
            fields.transaction_id,
            match.chip_id,
            code,
        )
        return await finish(WEBHOOK_FAILED, error=code, chip_id=uuid.UUID(match.chip_id))
    except Exception:
        logger.exception(
            "nedarim_webhook_subscription_failed transaction_id=%s chip_id=%s",
            fields.transaction_id,
            match.chip_id,
        )
        return await finish(
            WEBHOOK_FAILED, error="subscription_activate_failed", chip_id=uuid.UUID(match.chip_id)
        )

    return await finish(WEBHOOK_PROCESSED, chip_id=uuid.UUID(match.chip_id))
