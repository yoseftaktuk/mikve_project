from __future__ import annotations

import json
import logging
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gate_shared.errors import AppError

from .clients import FingerprintsClient
from .hebrew_calendar import current_hebrew_month
from .models import (
    PRODUCT_BALANCE,
    PRODUCT_MONTHLY_SUBSCRIPTION,
    STATUS_ABANDONED,
    STATUS_CREDITING,
    STATUS_FAILED,
    STATUS_PAID,
    STATUS_PENDING,
    CardTopup,
    NedarimCallback,
)
from .nedarim_plus import (
    NEDARIM_CALLBACK_IPS,
    CreateTransactionCommand,
    NedarimError,
    assert_callback_source_ip,
    shekels_from_cents,
    verify_callback,
)
from .payment_provider import PaymentProvider
from .settings import settings

logger = logging.getLogger(__name__)

PublishFn = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class CreatedCardTopup:
    """Result of opening a card top-up that the kiosk can clear in the iframe."""

    topup_id: uuid.UUID
    nedarim_transaction_id: str
    iframe_url: str
    amount_cents: int
    fingerprint_uid: str
    member_id: uuid.UUID
    product: str


def _normalize_product(product: str | None) -> str:
    value = (product or PRODUCT_BALANCE).strip().lower()
    if value not in (PRODUCT_BALANCE, PRODUCT_MONTHLY_SUBSCRIPTION):
        raise AppError(
            code="invalid_product",
            message=f"product must be {PRODUCT_BALANCE!r} or {PRODUCT_MONTHLY_SUBSCRIPTION!r}",
            http_status=400,
        )
    return value


def _require_allowed_amount(amount_cents: int, *, product: str) -> None:
    if product == PRODUCT_MONTHLY_SUBSCRIPTION:
        expected = settings.subscription_price_cents
        if amount_cents != expected:
            raise AppError(
                code="invalid_amount",
                message=f"Subscription amount must be {expected} agorot",
                http_status=400,
                details={"subscription_price_cents": expected},
            )
        return
    allowed = settings.topup_amount_options_cents
    if amount_cents not in allowed:
        raise AppError(
            code="invalid_amount",
            message=f"Amount must be one of {list(allowed)} agorot",
            http_status=400,
            details={"allowed_amounts_cents": list(allowed)},
        )


def _callback_url(topup_id: uuid.UUID) -> str:
    base = settings.public_base_url.rstrip("/")
    if not base:
        raise AppError(
            code="public_base_url_missing",
            message="PUBLIC_BASE_URL is not set; Nedarim cannot deliver the callback",
            http_status=503,
        )
    # [ID] is replaced by Nedarim with the transaction id (docs v=93).
    return f"{base}/api/payments/nedarim/callback/{topup_id}?nid=[ID]"


def _new_ajax_id() -> str:
    # Docs recommend a unique value such as a millisecond timestamp (v=93).
    return f"{int(time.time() * 1000)}{secrets.token_hex(3)}"


async def create_card_topup(
    *,
    fingerprint_uid: str,
    amount_cents: int,
    db: AsyncSession,
    member_client: FingerprintsClient,
    payment_provider: PaymentProvider,
    product: str = PRODUCT_BALANCE,
) -> CreatedCardTopup:
    """Resolve the member, open a pending top-up, and create the Nedarim transaction.

    The amount is fixed on this server before the iframe is shown, so the kiosk
    cannot alter what the cardholder is charged.
    """
    product = _normalize_product(product)
    _require_allowed_amount(amount_cents, product=product)
    if settings.payment_mode != "mock" and not settings.public_base_url.strip():
        raise AppError(
            code="public_base_url_missing",
            message="PUBLIC_BASE_URL is not set; Nedarim cannot deliver the callback",
            http_status=503,
        )

    uid = fingerprint_uid.strip()
    if not uid:
        raise AppError(code="invalid_fingerprint_uid", message="fingerprint_uid is required", http_status=400)

    try:
        member = await member_client.validate(uid)
    except ValueError:
        raise AppError(code="member_not_found", message="Member not found", http_status=404) from None

    if not member.is_enabled:
        raise AppError(code="member_disabled", message="Member is disabled", http_status=409)

    hebrew = current_hebrew_month()
    if product == PRODUCT_MONTHLY_SUBSCRIPTION and member.subscription_active:
        raise AppError(
            code="subscription_already_active",
            message="Member already has an active subscription for this Hebrew month",
            http_status=409,
            details={"hebrew_month_name": member.subscription_month_name or hebrew.name},
        )

    topup = CardTopup(
        id=uuid.uuid4(),
        member_id=uuid.UUID(member.member_id),
        fingerprint_uid=member.uid,
        amount_cents=amount_cents,
        product=product,
        status=STATUS_PENDING,
        ajax_id=_new_ajax_id(),
    )
    db.add(topup)
    await db.flush()

    callback_url = (
        _callback_url(topup.id)
        if settings.payment_mode != "mock"
        else f"mock://local/card-topups/{topup.id}"
    )
    if product == PRODUCT_MONTHLY_SUBSCRIPTION:
        comment = f"gate monthly subscription {member.uid} {hebrew.name}"
    else:
        comment = f"gate top-up {member.uid}"
    command = CreateTransactionCommand(
        amount_cents=amount_cents,
        callback_url=callback_url,
        ajax_id=topup.ajax_id,
        param1=str(topup.id),
        comment=comment,
        groupe=settings.nedarim_groupe,
    )

    try:
        result = await payment_provider.create_transaction(command)
    except NedarimError as exc:
        topup.status = STATUS_FAILED
        topup.error_code = exc.code
        await db.commit()
        logger.warning(
            "card_topup_create_failed topup_id=%s code=%s message=%s",
            topup.id,
            exc.code,
            exc.message,
        )
        raise AppError(code=exc.code, message=exc.message, http_status=502) from None

    topup.nedarim_created_id = result.transaction_id
    await db.commit()
    await db.refresh(topup)

    logger.info(
        "card_topup_created topup_id=%s product=%s fingerprint_uid=%s amount_cents=%s nedarim_id=%s",
        topup.id,
        topup.product,
        topup.fingerprint_uid,
        topup.amount_cents,
        topup.nedarim_created_id,
    )
    return CreatedCardTopup(
        topup_id=topup.id,
        nedarim_transaction_id=result.transaction_id,
        iframe_url=payment_provider.iframe_url,
        amount_cents=topup.amount_cents,
        fingerprint_uid=topup.fingerprint_uid,
        member_id=topup.member_id,
        product=topup.product,
    )


def _mock_callback_payload(topup: CardTopup) -> dict[str, Any]:
    """Build a Nedarim-shaped callback body for mock simulate-pay."""
    transaction_id = topup.nedarim_created_id or f"MOCK-{topup.id.hex[:12]}"
    return {
        "TransactionId": transaction_id,
        "Amount": shekels_from_cents(topup.amount_cents),
        "Currency": "1",
        "Confirmation": "MOCK-CONF",
        "LastNum": "4242",
        "Param1": str(topup.id),
    }


async def simulate_mock_card_payment(
    *,
    topup_id: uuid.UUID,
    db: AsyncSession,
    member_client: FingerprintsClient,
    publish: PublishFn | None = None,
) -> CallbackHandlingResult:
    """Credit a pending mock top-up via the same callback path as production."""
    if settings.payment_mode != "mock":
        return CallbackHandlingResult(
            accepted=False,
            http_status=403,
            code="mock_only",
            message="Simulate pay is only available in mock payment mode",
        )

    topup = await db.get(CardTopup, topup_id)
    if topup is None:
        return CallbackHandlingResult(
            accepted=False,
            http_status=404,
            code="topup_not_found",
            message="Top-up not found",
        )

    if topup.status == STATUS_PAID:
        return CallbackHandlingResult(
            accepted=True,
            http_status=200,
            code="already_paid",
            message="Top-up already paid",
        )

    mock_ip = next(iter(NEDARIM_CALLBACK_IPS))
    return await process_nedarim_callback(
        topup_id=topup_id,
        payload=_mock_callback_payload(topup),
        source_ip=mock_ip,
        db=db,
        member_client=member_client,
        publish=publish,
    )


async def get_card_topup(topup_id: uuid.UUID, db: AsyncSession) -> CardTopup:
    """Return a top-up row or raise 404."""
    topup = await db.get(CardTopup, topup_id)
    if topup is None:
        raise AppError(code="topup_not_found", message="Top-up not found", http_status=404)
    return topup


async def abandon_card_topup(topup_id: uuid.UUID, db: AsyncSession) -> CardTopup:
    """Mark a still-pending top-up as abandoned when the user cancels."""
    topup = await get_card_topup(topup_id, db)
    if topup.status != STATUS_PENDING:
        raise AppError(
            code="topup_not_pending",
            message=f"Top-up cannot be abandoned from status {topup.status}",
            http_status=409,
        )
    topup.status = STATUS_ABANDONED
    await db.commit()
    await db.refresh(topup)
    logger.info("card_topup_abandoned topup_id=%s", topup.id)
    return topup


@dataclass(frozen=True)
class CallbackHandlingResult:
    """Outcome of a Nedarim CallBack, for the HTTP layer to translate to a status code."""

    accepted: bool
    http_status: int
    code: str
    message: str


async def _write_callback_audit(
    db: AsyncSession,
    *,
    topup_id: uuid.UUID | None,
    source_ip: str | None,
    accepted: bool,
    rejection_reason: str | None,
    payload: dict[str, Any],
) -> None:
    db.add(
        NedarimCallback(
            topup_id=topup_id,
            source_ip=source_ip,
            accepted=accepted,
            rejection_reason=rejection_reason,
            payload_json=json.dumps(payload, ensure_ascii=False, default=str),
        )
    )
    await db.commit()


async def claim_pending_topup(db: AsyncSession, topup_id: uuid.UUID) -> CardTopup | None:
    """Atomically move pending → crediting. Returns None if another worker won."""
    result = await db.execute(
        update(CardTopup)
        .where(CardTopup.id == topup_id, CardTopup.status == STATUS_PENDING)
        .values(status=STATUS_CREDITING)
    )
    await db.commit()
    if result.rowcount != 1:
        return None
    return await db.get(CardTopup, topup_id)


async def _credit_and_settle(
    topup: CardTopup,
    *,
    db: AsyncSession,
    member_client: FingerprintsClient,
    transaction_id: str,
    confirmation: str | None,
    last_num: str | None,
    publish: PublishFn | None,
) -> int:
    """Credit balance or activate subscription (idempotent) and mark the top-up paid."""
    product = topup.product or PRODUCT_BALANCE
    if product == PRODUCT_MONTHLY_SUBSCRIPTION:
        hebrew = current_hebrew_month()
        try:
            balance_after = await member_client.activate_subscription(
                str(topup.member_id),
                amount_cents=topup.amount_cents,
                nedarim_transaction_id=transaction_id,
                hebrew_year=hebrew.year,
                hebrew_month=hebrew.month,
                hebrew_month_name=hebrew.name,
            )
        except ValueError as exc:
            code = str(exc) or "subscription_activate_failed"
            raise AppError(code=code, message="Could not activate monthly subscription", http_status=502) from None
        event_type = "subscription.paid"
    else:
        balance_after = await member_client.adjust_balance(
            member_id=str(topup.member_id),
            delta_cents=topup.amount_cents,
            reason="card_topup",
            description=f"Nedarim Plus top-up {transaction_id}",
            idempotency_key=f"nedarim:{transaction_id}",
        )
        event_type = "card_topup.paid"

    topup.status = STATUS_PAID
    topup.nedarim_transaction_id = transaction_id
    topup.confirmation = confirmation
    topup.last_num = last_num
    topup.balance_after_cents = balance_after
    topup.credited_at = datetime.now(timezone.utc)
    topup.error_code = None
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Another row already claimed this Nedarim transaction id.
        existing = await db.get(CardTopup, topup.id)
        if existing is not None and existing.status == STATUS_PAID:
            return int(existing.balance_after_cents or balance_after)
        raise AppError(
            code="duplicate_transaction",
            message="This Nedarim transaction was already credited",
            http_status=409,
        ) from None

    if publish is not None:
        await publish(
            {
                "type": event_type,
                "topup_id": str(topup.id),
                "member_id": str(topup.member_id),
                "fingerprint_uid": topup.fingerprint_uid,
                "product": product,
                "amount_cents": topup.amount_cents,
                "balance_after_cents": balance_after,
                "nedarim_transaction_id": transaction_id,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
    return balance_after


async def process_nedarim_callback(
    *,
    topup_id: uuid.UUID,
    payload: dict[str, Any],
    source_ip: str | None,
    db: AsyncSession,
    member_client: FingerprintsClient,
    publish: PublishFn | None = None,
) -> CallbackHandlingResult:
    """Verify a CallBack and credit the balance. Safe to call more than once.

    Documentation (v=95): IP allowlist, known unused correlation id, amount match.
    Crediting happens only after a successful pending → crediting claim.
    """
    # IP first — even unknown top-up ids must not skip the allowlist.
    try:
        assert_callback_source_ip(source_ip)
    except NedarimError as exc:
        await _write_callback_audit(
            db,
            topup_id=None,
            source_ip=source_ip,
            accepted=False,
            rejection_reason=exc.code,
            payload=payload,
        )
        return CallbackHandlingResult(
            accepted=False,
            http_status=403,
            code=exc.code,
            message=exc.message,
        )

    topup = await db.get(CardTopup, topup_id)
    if topup is None:
        await _write_callback_audit(
            db,
            topup_id=None,
            source_ip=source_ip,
            accepted=False,
            rejection_reason="unknown_topup",
            payload=payload,
        )
        return CallbackHandlingResult(
            accepted=False,
            http_status=404,
            code="unknown_topup",
            message="Top-up not found",
        )

    try:
        parsed = verify_callback(
            payload=payload,
            source_ip=source_ip,
            expected_amount_cents=topup.amount_cents,
            expected_topup_id=str(topup.id),
        )
    except NedarimError as exc:
        await _write_callback_audit(
            db,
            topup_id=topup.id,
            source_ip=source_ip,
            accepted=False,
            rejection_reason=exc.code,
            payload=payload,
        )
        http_status = 403 if exc.code == "bad_ip" else 400
        return CallbackHandlingResult(
            accepted=False,
            http_status=http_status,
            code=exc.code,
            message=exc.message,
        )

    # CreateTransaction returns an iframe/session id; the CallBack often reports a
    # different cleared TransactionId (observed live: create 1766827 → callback 76030570).
    # Association is the URL topup_id + Param1 + amount + source IP — not this equality.
    if topup.nedarim_created_id and parsed.transaction_id != topup.nedarim_created_id:
        logger.info(
            "nedarim_id_differs topup_id=%s created_id=%s callback_id=%s",
            topup.id,
            topup.nedarim_created_id,
            parsed.transaction_id,
        )

    if topup.status == STATUS_PAID:
        await _write_callback_audit(
            db,
            topup_id=topup.id,
            source_ip=source_ip,
            accepted=True,
            rejection_reason="already_paid",
            payload=payload,
        )
        return CallbackHandlingResult(
            accepted=True,
            http_status=200,
            code="already_paid",
            message="Top-up already credited",
        )

    if topup.status in (STATUS_ABANDONED, STATUS_FAILED):
        await _write_callback_audit(
            db,
            topup_id=topup.id,
            source_ip=source_ip,
            accepted=False,
            rejection_reason="not_pending",
            payload=payload,
        )
        return CallbackHandlingResult(
            accepted=False,
            http_status=409,
            code="not_pending",
            message=f"Top-up is {topup.status}",
        )

    if topup.status == STATUS_CREDITING:
        # Previous attempt died after the claim — retry credit using the
        # idempotency key so the balance cannot be applied twice.
        claimed = topup
    else:
        claimed = await claim_pending_topup(db, topup.id)
        if claimed is None:
            # Lost the race; reload and treat paid as success.
            current = await db.get(CardTopup, topup.id)
            if current is not None and current.status == STATUS_PAID:
                await _write_callback_audit(
                    db,
                    topup_id=topup.id,
                    source_ip=source_ip,
                    accepted=True,
                    rejection_reason="already_paid",
                    payload=payload,
                )
                return CallbackHandlingResult(
                    accepted=True,
                    http_status=200,
                    code="already_paid",
                    message="Top-up already credited",
                )
            await _write_callback_audit(
                db,
                topup_id=topup.id,
                source_ip=source_ip,
                accepted=False,
                rejection_reason="claim_failed",
                payload=payload,
            )
            return CallbackHandlingResult(
                accepted=False,
                http_status=409,
                code="claim_failed",
                message="Could not claim top-up for crediting",
            )

    try:
        balance_after = await _credit_and_settle(
            claimed,
            db=db,
            member_client=member_client,
            transaction_id=parsed.transaction_id,
            confirmation=parsed.confirmation,
            last_num=parsed.last_num,
            publish=publish,
        )
    except Exception:
        logger.exception(
            "card_topup_credit_failed topup_id=%s nedarim_id=%s",
            topup.id,
            parsed.transaction_id,
        )
        claimed.error_code = "member_credit_failed"
        await db.commit()
        await _write_callback_audit(
            db,
            topup_id=topup.id,
            source_ip=source_ip,
            accepted=False,
            rejection_reason="member_credit_failed",
            payload=payload,
        )
        return CallbackHandlingResult(
            accepted=False,
            http_status=500,
            code="member_credit_failed",
            message="Payment received but balance credit failed; staff must repair",
        )

    await _write_callback_audit(
        db,
        topup_id=topup.id,
        source_ip=source_ip,
        accepted=True,
        rejection_reason=None,
        payload=payload,
    )
    logger.info(
        "card_topup_paid topup_id=%s fingerprint_uid=%s amount_cents=%s balance_after=%s nedarim_id=%s",
        topup.id,
        topup.fingerprint_uid,
        topup.amount_cents,
        balance_after,
        parsed.transaction_id,
    )
    return CallbackHandlingResult(
        accepted=True,
        http_status=200,
        code="ok",
        message="Top-up credited",
    )
