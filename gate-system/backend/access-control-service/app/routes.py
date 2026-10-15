"""HTTP and WebSocket routes for the access-control service."""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gate_shared.errors import AppError

from .access_logic import process_cash_inserted
from .db import get_db
from .fingerprint_logic import approve_pending
from .management import (
    MemberInfoResponse,
    MemberTopupRequest,
    MemberTopupResponse,
    ManagementAuthResponse,
    ManagementPinRequest,
    ManagementPricingResponse,
    ManagementPricingUpdateRequest,
    ManagementSessionResponse,
    ManagementUserResponse,
    ManagementUserUpdateRequest,
    authenticate_pin,
    delete_user as management_delete_user,
    get_chip_info,
    get_management_session,
    list_users as management_list_users,
    logout_management,
    open_door as management_open_door,
    require_management_token,
    topup_chip,
    update_user as management_update_user,
)
from .models import AccessLog, HardwareEvent
from .pricing import get_entrance_fee_cents, get_pricing, update_pricing
from .schemas import (
    AccessDecisionResponse,
    AccessLogResponse,
    FingerprintApprovalRequest,
    FingerprintEnrollCancelRequest,
    FingerprintEnrollRequest,
    FingerprintEnrollStartResponse,
    SimulateCashRequest,
    SimulateCashResponse,
)
from .settings import settings
from .statistics import ManagementStatisticsResponse, VALID_RANGES, fetch_management_statistics
from . import state

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/healthz")
async def healthz(db: AsyncSession = Depends(get_db)):
    entrance_fee_cents = await get_entrance_fee_cents(db)
    return {
        "status": "ok",
        "service": settings.service_name,
        "cash_accumulated_cents": state.cash_session.accumulated_cents,
        "entrance_fee_cents": entrance_fee_cents,
        "door_unlock_seconds": settings.door_unlock_seconds,
        "cash_session_timeout_seconds": settings.cash_session_timeout_seconds,
    }


@router.get("/access/logs", response_model=list[AccessLogResponse])
async def access_logs(db: AsyncSession = Depends(get_db), limit: int = 50):
    """Return recent access grant/deny log entries."""
    limit = max(1, min(limit, 200))
    rows = (await db.execute(select(AccessLog).order_by(AccessLog.id.desc()).limit(limit))).scalars().all()
    return [AccessLogResponse.model_validate(r, from_attributes=True) for r in rows]


@router.post("/fingerprint/approve", response_model=AccessDecisionResponse)
async def fingerprint_approve(req: FingerprintApprovalRequest, db: AsyncSession = Depends(get_db)):
    """Confirm a scanned fingerprint: charge the fee and open the door."""
    try:
        return await approve_pending(
            req.approval_id,
            db,
            member_client=state.member_client,
            hardware_client=state.hardware_client,
            publish=state.publish,
            approvals=state.approvals,
            cash_session=state.cash_session,
        )
    except ValueError as exc:
        raise AppError(
            code=str(exc),
            message="This approval is no longer available",
            http_status=status.HTTP_409_CONFLICT,
        ) from None


@router.post("/fingerprint/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def fingerprint_cancel(req: FingerprintApprovalRequest):
    """Dismiss a pending fingerprint approval without charging."""
    await state.approvals.clear(req.approval_id, reason="cancelled")
    return None


@router.post("/dev/simulate/cash", response_model=SimulateCashResponse, include_in_schema=False)
async def dev_simulate_cash(req: SimulateCashRequest, db: AsyncSession = Depends(get_db)):
    """Simulate cash insertion in development mode."""
    if settings.environment != "dev":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    granted, remaining_or_accumulated = await process_cash_inserted(
        req.amount_cents,
        db,
        cash_session=state.cash_session,
        hardware_client=state.hardware_client,
        publish=state.publish,
    )
    return SimulateCashResponse(
        granted=granted,
        accumulated_cents=state.cash_session.accumulated_cents,
        entrance_fee_cents=await get_entrance_fee_cents(db),
        remaining_cents=remaining_or_accumulated if granted else 0,
    )


@router.post("/management/auth", response_model=ManagementAuthResponse, include_in_schema=False)
async def management_auth(req: ManagementPinRequest, response: Response):
    """Authenticate the management PIN and set an HttpOnly session cookie."""
    return await authenticate_pin(req, response)


@router.get("/management/session", response_model=ManagementSessionResponse, include_in_schema=False)
async def management_session(request: Request):
    """Return whether the current request has a valid management session."""
    return get_management_session(request)


@router.post("/management/logout", response_model=ManagementSessionResponse, include_in_schema=False)
async def management_logout(request: Request, response: Response):
    """Revoke the management session and clear the HttpOnly cookie."""
    return await logout_management(request, response)


@router.get(
    "/management/chip/{uid}",
    response_model=MemberInfoResponse,
    include_in_schema=False,
)
async def management_chip_info(uid: str):
    """Return member balance and status for desk top-up / management UI."""
    return await get_chip_info(uid, state.member_client)


@router.post(
    "/management/chip/topup",
    response_model=MemberTopupResponse,
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_chip_topup(req: MemberTopupRequest):
    """Top up a member balance from the management panel."""
    return await topup_chip(req, state.member_client)


@router.get(
    "/management/users",
    response_model=list[ManagementUserResponse],
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_users_list():
    """List registered fingerprint ledger users."""
    return await management_list_users(state.member_client)


@router.get(
    "/management/statistics",
    response_model=ManagementStatisticsResponse,
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_statistics(
    range: str = "today",
    db: AsyncSession = Depends(get_db),
):
    """Return aggregated management KPIs for the selected date range."""
    if range not in VALID_RANGES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_range")
    return await fetch_management_statistics(db, range)


@router.get(
    "/management/pricing",
    response_model=ManagementPricingResponse,
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_pricing_get(db: AsyncSession = Depends(get_db)):
    """Return the current entrance fee and monthly subscription price."""
    pricing = await get_pricing(db)
    return ManagementPricingResponse(
        entrance_fee_cents=pricing.entrance_fee_cents,
        subscription_price_cents=pricing.subscription_price_cents,
    )


@router.patch(
    "/management/pricing",
    response_model=ManagementPricingResponse,
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_pricing_update(
    req: ManagementPricingUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update entrance fee and/or monthly subscription price."""
    try:
        pricing = await update_pricing(
            db,
            entrance_fee_cents=req.entrance_fee_cents if "entrance_fee_cents" in req.model_fields_set else None,
            subscription_price_cents=(
                req.subscription_price_cents if "subscription_price_cents" in req.model_fields_set else None
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    return ManagementPricingResponse(
        entrance_fee_cents=pricing.entrance_fee_cents,
        subscription_price_cents=pricing.subscription_price_cents,
    )


@router.patch(
    "/management/users/{member_id}",
    response_model=ManagementUserResponse,
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_users_update(member_id: str, req: ManagementUserUpdateRequest):
    """Edit a registered user's name and/or enabled flag."""
    return await management_update_user(member_id, req, state.member_client)


@router.delete(
    "/management/users/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_users_delete(member_id: str):
    """Delete a registered user and clear their fingerprint template when present."""
    await management_delete_user(member_id, state.member_client, state.hardware_client)
    return None


@router.post(
    "/management/cash-receipts/redeem",
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_redeem_cash_receipt(body: dict, db: AsyncSession = Depends(get_db)):
    """Mark a cash compensation receipt as paid out from the till."""
    if state.orchestrator is None:
        raise AppError(code="saga_unavailable", message="Saga not started", http_status=503)
    code = str(body.get("redeem_code", "")).strip()
    staff_id = str(body.get("staff_id") or "management")
    try:
        receipt = await state.orchestrator.redeem_receipt(code, staff_id, db)
    except ValueError as exc:
        raise AppError(code=str(exc), message=str(exc), http_status=409) from None
    return {
        "receipt_id": str(receipt.id),
        "attempt_id": str(receipt.attempt_id),
        "amount_cents": receipt.amount_cents,
        "status": receipt.status,
        "redeemed_at": receipt.redeemed_at.isoformat() if receipt.redeemed_at else None,
    }


@router.post(
    "/management/access-attempts/{attempt_id}/resolve",
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_resolve_attempt(attempt_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    """Staff resolves a MANUAL_REVIEW attempt after handling compensation offline."""
    if state.orchestrator is None:
        raise AppError(code="saga_unavailable", message="Saga not started", http_status=503)
    note = str(body.get("note") or "resolved")
    try:
        attempt = await state.orchestrator.resolve_manual_review(uuid.UUID(attempt_id), note, db)
    except ValueError as exc:
        raise AppError(code=str(exc), message=str(exc), http_status=409) from None
    return {"attempt_id": str(attempt.id), "status": attempt.status}


@router.post(
    "/management/door/open",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_door_open():
    """Manually open the door from the management panel."""
    await management_open_door(state.hardware_client)
    return None


@router.post(
    "/management/fingerprint/enroll",
    response_model=FingerprintEnrollStartResponse,
    include_in_schema=False,
)
async def management_fingerprint_enroll(req: FingerprintEnrollRequest):
    """Start enrolling a finger for a named holder with an optional initial balance."""
    from gate_shared.national_id import InvalidNationalIdError, normalize_national_id

    try:
        national_id = normalize_national_id(req.national_id)
    except InvalidNationalIdError:
        raise AppError(
            code="invalid_national_id",
            message="Invalid Israeli national ID",
            http_status=status.HTTP_400_BAD_REQUEST,
        ) from None

    session = state.enrollments.create(
        holder_name=req.holder_name.strip(),
        national_id=national_id,
        initial_amount_cents=req.initial_amount_cents,
    )
    try:
        await state.hardware_client.enroll_fingerprint(session.session_id)
    except Exception:
        state.enrollments.discard(session.session_id)
        logger.exception("fingerprint_enroll_start_failed session_id=%s", session.session_id)
        raise AppError(
            code="fingerprint_reader_unavailable",
            message="Could not start the fingerprint reader",
            http_status=status.HTTP_502_BAD_GATEWAY,
        ) from None
    return FingerprintEnrollStartResponse(
        session_id=session.session_id,
        holder_name=session.holder_name,
        national_id=session.national_id,
        initial_amount_cents=session.initial_amount_cents,
    )


@router.post(
    "/management/fingerprint/enroll/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
)
async def management_fingerprint_enroll_cancel(req: FingerprintEnrollCancelRequest):
    """Abort a running enrollment; keep the session until enrolled cleanup or TTL."""
    if state.enrollments.mark_cancelling(req.session_id):
        await state.hardware_client.cancel_enroll()
    return None


@router.post(
    "/management/fingerprint/identify/start",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
)
async def management_fingerprint_identify_start():
    """Listen for fingerprint scans without creating door approvals."""
    await state.identify_sessions.start()
    return None


@router.post(
    "/management/fingerprint/identify/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
)
async def management_fingerprint_identify_cancel():
    """Stop desk identify mode and restore entrance scan behavior."""
    await state.identify_sessions.cancel()
    return None


@router.post("/hardware/events", status_code=status.HTTP_204_NO_CONTENT)
async def ingest_hardware_event(event: dict, db: AsyncSession = Depends(get_db)):
    """Persist a raw hardware event payload for auditing."""
    db.add(HardwareEvent(event_type=str(event.get("type", "unknown")), payload_json=json.dumps(event)))
    await db.commit()
    return None


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    """Stream live gate events to the dashboard over WebSocket."""
    await ws.accept()
    await state.fanout.register(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        state.fanout.unregister(ws)
    except Exception:
        state.fanout.unregister(ws)
        await ws.close()
