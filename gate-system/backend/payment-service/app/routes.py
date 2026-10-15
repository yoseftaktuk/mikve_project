"""HTTP routes for the payment service."""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .client_ip import resolve_callback_source_ip
from .db import get_db
from .hebrew_calendar import current_hebrew_month
from .models import STATUS_CREDITING, STATUS_PENDING, CardTopup
from .pricing import get_subscription_price_cents
from .provider import charge_credit_card
from .schemas import (
    CardTopupCreateRequest,
    CardTopupCreateResponse,
    CardTopupStatusResponse,
    ChargeMemberRequest,
    ChargeMemberResponse,
)
from .settings import settings
from .topup_logic import (
    abandon_card_topup,
    create_card_topup,
    get_card_topup,
    process_nedarim_callback,
    simulate_mock_card_payment,
)
from .webhook_logic import process_nedarim_webhook, webhook_allows_local_bypass
from . import state

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/healthz")
async def healthz(db: AsyncSession = Depends(get_db)):
    """Health plus stuck-row counts so staff can see mid-credit failures."""
    pending = await db.scalar(
        select(func.count()).select_from(CardTopup).where(CardTopup.status == STATUS_PENDING)
    )
    crediting = await db.scalar(
        select(func.count()).select_from(CardTopup).where(CardTopup.status == STATUS_CREDITING)
    )
    hebrew = current_hebrew_month()
    subscription_price_cents = await get_subscription_price_cents(db)
    return {
        "status": "ok",
        "service": settings.service_name,
        "payment_mode": settings.payment_mode,
        "nedarim_configured": state.payment_provider.is_configured,
        "public_base_url_set": bool(settings.public_base_url),
        "topup_amounts_cents": list(settings.topup_amount_options_cents),
        "subscription_price_cents": subscription_price_cents,
        "current_hebrew_month_name": hebrew.name,
        "pending_topups": int(pending or 0),
        "crediting_topups": int(crediting or 0),
    }


def _status_response(topup: CardTopup) -> CardTopupStatusResponse:
    return CardTopupStatusResponse(
        topup_id=topup.id,
        status=topup.status,
        amount_cents=topup.amount_cents,
        fingerprint_uid=topup.fingerprint_uid,
        member_id=topup.member_id,
        product=topup.product or "balance",
        nedarim_transaction_id=topup.nedarim_created_id or topup.nedarim_transaction_id,
        balance_after_cents=topup.balance_after_cents,
        last_num=topup.last_num,
        error_code=topup.error_code,
    )


@router.post("/card-topups", response_model=CardTopupCreateResponse)
async def card_topups_create(req: CardTopupCreateRequest, db: AsyncSession = Depends(get_db)):
    """Open a pending card top-up and create the Nedarim transaction server-side."""
    created = await create_card_topup(
        fingerprint_uid=req.fingerprint_uid,
        amount_cents=req.amount_cents,
        product=req.product,
        db=db,
        member_client=state.member_client,
        payment_provider=state.payment_provider,
    )
    return CardTopupCreateResponse(
        topup_id=created.topup_id,
        nedarim_transaction_id=created.nedarim_transaction_id,
        iframe_url=created.iframe_url,
        amount_cents=created.amount_cents,
        fingerprint_uid=created.fingerprint_uid,
        member_id=created.member_id,
        product=created.product,
    )


@router.get("/card-topups/{topup_id}", response_model=CardTopupStatusResponse)
async def card_topups_status(topup_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Return the server-confirmed status. The only thing the kiosk may trust."""
    topup = await get_card_topup(topup_id, db)
    return _status_response(topup)


@router.post("/card-topups/{topup_id}/abandon", response_model=CardTopupStatusResponse)
async def card_topups_abandon(topup_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Mark a still-pending top-up abandoned when the user cancels."""
    topup = await abandon_card_topup(topup_id, db)
    return _status_response(topup)


@router.post("/dev/card-topups/{topup_id}/simulate-pay", include_in_schema=False)
async def dev_simulate_card_topup_pay(topup_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Mock mode only: deliver a fake Nedarim CallBack and credit the chip."""
    result = await simulate_mock_card_payment(
        topup_id=topup_id,
        db=db,
        member_client=state.member_client,
        publish=state.publish_payment_event,
    )
    topup = await db.get(CardTopup, topup_id)
    balance_after = topup.balance_after_cents if topup is not None else None
    return JSONResponse(
        status_code=result.http_status,
        content={
            "status": "ok" if result.accepted else "error",
            "code": result.code,
            "message": result.message,
            "balance_after_cents": balance_after,
        },
    )


@router.post("/nedarim/callback/{topup_id}")
async def nedarim_callback(
    topup_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Nedarim Plus server-to-server CallBack. Sole authority for crediting balance.

    Sent once, never retried (docs v=95). Always audit; credit only after IP,
    amount, and single-use claim checks pass.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "code": "bad_json", "message": "Body must be JSON"},
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "code": "bad_payload", "message": "Body must be a JSON object"},
        )

    if settings.nedarim_require_cloudflare and not request.headers.get("cf-connecting-ip"):
        return JSONResponse(
            status_code=403,
            content={
                "status": "error",
                "code": "missing_cf",
                "message": "Callback must arrive through Cloudflare Tunnel",
            },
        )

    source_ip = resolve_callback_source_ip(request)
    result = await process_nedarim_callback(
        topup_id=topup_id,
        payload=payload,
        source_ip=source_ip,
        db=db,
        member_client=state.member_client,
        publish=state.publish_payment_event,
    )
    return JSONResponse(
        status_code=result.http_status,
        content={
            "status": "ok" if result.accepted else "error",
            "code": result.code,
            "message": result.message,
        },
    )


@router.post("/nedarim/webhook")
async def nedarim_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Institution-level Nedarim webhook. Identifies the chip by Zeout only."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "code": "bad_json", "message": "Body must be JSON"},
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "code": "bad_payload", "message": "Body must be a JSON object"},
        )

    allow_local = webhook_allows_local_bypass()
    if (
        settings.nedarim_require_cloudflare
        and not allow_local
        and not request.headers.get("cf-connecting-ip")
    ):
        return JSONResponse(
            status_code=403,
            content={
                "status": "error",
                "code": "missing_cf",
                "message": "Callback must arrive through Cloudflare Tunnel",
            },
        )

    source_ip = resolve_callback_source_ip(request)
    result = await process_nedarim_webhook(
        payload=payload,
        source_ip=source_ip,
        db=db,
        member_client=state.member_client,
        skip_source_ip=allow_local,
    )
    return JSONResponse(
        status_code=result.http_status,
        content={
            "status": "ok" if result.accepted else "error",
            "code": result.code,
            "message": result.message,
        },
    )


@router.post("/charge-member", response_model=ChargeMemberResponse)
async def charge_member(req: ChargeMemberRequest):
    """Legacy stub. Prefer POST /card-topups — this path does not credit a balance."""
    charge_credit_card(amount=req.amount)
    if state.redis_client is not None:
        await state.redis_client.publish(
            "payment.events",
            json.dumps({"type": "member.charged", "amount": req.amount}),
        )
    logger.info("member_charged amount=%s", req.amount)
    return ChargeMemberResponse()
