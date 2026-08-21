import json
import logging
import uuid

import redis.asyncio as redis
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gate_shared.errors import AppError, ErrorResponse
from gate_shared.logging import configure_logging

from .access_logic import CashSession, process_cash_inserted
from .clients import FingerprintsClient, HardwareClient
from .db import engine, get_db
from .fingerprint_logic import (
    PendingApprovalStore,
    PendingEnrollmentStore,
    TopupIdentifyStore,
    approve_pending,
)
from .hardware_consumer import HardwareEventConsumer
from .models import AccessLog, Base, HardwareEvent
from .realtime import PubSubFanout
from .saga import AccessAttemptReconciler, AccessOrchestrator
from .saga import models as saga_models  # noqa: F401 — register saga tables on Base.metadata
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
from .management import (
    MemberInfoResponse,
    MemberTopupRequest,
    MemberTopupResponse,
    ManagementAuthResponse,
    ManagementPinRequest,
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
from .settings import settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Access Control Service",
    version="0.1.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

member_client = FingerprintsClient()
hardware_client = HardwareClient()
cash_session = CashSession(timeout_seconds=settings.cash_session_timeout_seconds)
approvals = PendingApprovalStore(timeout_seconds=settings.fingerprint_approval_timeout_seconds)
enrollments = PendingEnrollmentStore()
identify_sessions = TopupIdentifyStore()
fanout = PubSubFanout(settings.redis_url)
hardware_consumer: HardwareEventConsumer | None = None
redis_client: redis.Redis | None = None
orchestrator: AccessOrchestrator | None = None
reconciler: AccessAttemptReconciler | None = None


@app.on_event("startup")
async def startup() -> None:
    """Create tables, connect Redis, and start event consumers."""
    global redis_client, hardware_consumer, orchestrator, reconciler
    configure_logging(settings.service_name, settings.log_level)
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            f"""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '{settings.postgres_schema}'
                  AND table_name = 'access_attempts'
                  AND column_name = 'chip_id'
              ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '{settings.postgres_schema}'
                  AND table_name = 'access_attempts'
                  AND column_name = 'member_id'
              ) THEN
                EXECUTE 'ALTER TABLE {settings.postgres_schema}.access_attempts RENAME COLUMN chip_id TO member_id';
              END IF;
            END $$;
            """
        )
        await conn.exec_driver_sql(
            f"""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '{settings.postgres_schema}'
                  AND table_name = 'access_logs'
                  AND column_name = 'chip_id'
              ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '{settings.postgres_schema}'
                  AND table_name = 'access_logs'
                  AND column_name = 'member_id'
              ) THEN
                EXECUTE 'ALTER TABLE {settings.postgres_schema}.access_logs RENAME COLUMN chip_id TO member_id';
              END IF;
            END $$;
            """
        )
        await conn.run_sync(Base.metadata.create_all)
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    cash_session.set_publish(_publish)
    approvals.set_publish(_publish)
    orchestrator = AccessOrchestrator(
        member_client=member_client,
        hardware_client=hardware_client,
        cash_session=cash_session,
        publish=_publish,
    )
    await fanout.start()
    hardware_consumer = HardwareEventConsumer(
        settings.redis_url,
        member_client=member_client,
        hardware_client=hardware_client,
        cash_session=cash_session,
        publish=_publish,
        approvals=approvals,
        enrollments=enrollments,
        identify=identify_sessions,
    )
    await hardware_consumer.start()
    if settings.access_saga_enabled:
        reconciler = AccessAttemptReconciler(orchestrator)
        await reconciler.start()
    logger.info(
        "startup_complete entrance_fee_cents=%s door_unlock_seconds=%s cash_session_timeout_seconds=%s access_saga_enabled=%s",
        settings.entrance_fee_cents,
        settings.door_unlock_seconds,
        settings.cash_session_timeout_seconds,
        settings.access_saga_enabled,
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    """Stop consumers and close Redis on service shutdown."""
    global redis_client, hardware_consumer, reconciler
    if reconciler is not None:
        await reconciler.stop()
        reconciler = None
    await cash_session.shutdown()
    await approvals.shutdown()
    enrollments.clear()
    identify_sessions.clear()
    if hardware_consumer is not None:
        await hardware_consumer.stop()
        hardware_consumer = None
    await fanout.stop()
    if redis_client is not None:
        await redis_client.aclose()
        redis_client = None


@app.exception_handler(AppError)
async def app_error_handler(_, exc: AppError):
    """Convert AppError exceptions into JSON error responses."""
    return JSONResponse(
        status_code=exc.http_status,
        content=ErrorResponse(code=exc.code, message=exc.message, details=exc.details).model_dump(),
    )


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "service": settings.service_name,
        "cash_accumulated_cents": cash_session.accumulated_cents,
        "entrance_fee_cents": settings.entrance_fee_cents,
        "door_unlock_seconds": settings.door_unlock_seconds,
        "cash_session_timeout_seconds": settings.cash_session_timeout_seconds,
    }


async def _publish(event: dict) -> None:
    """Publish an access event to Redis and connected dashboards."""
    if redis_client is None:
        return
    await redis_client.publish("access.events", json.dumps(event))
    await fanout.publish_local(event)


@app.get("/access/logs", response_model=list[AccessLogResponse])
async def access_logs(db: AsyncSession = Depends(get_db), limit: int = 50):
    """Return recent access grant/deny log entries."""
    limit = max(1, min(limit, 200))
    rows = (await db.execute(select(AccessLog).order_by(AccessLog.id.desc()).limit(limit))).scalars().all()
    return [AccessLogResponse.model_validate(r, from_attributes=True) for r in rows]


@app.post("/fingerprint/approve", response_model=AccessDecisionResponse)
async def fingerprint_approve(req: FingerprintApprovalRequest, db: AsyncSession = Depends(get_db)):
    """Confirm a scanned fingerprint: charge the fee and open the door."""
    try:
        return await approve_pending(
            req.approval_id,
            db,
            member_client=member_client,
            hardware_client=hardware_client,
            publish=_publish,
            approvals=approvals,
            cash_session=cash_session,
        )
    except ValueError as exc:
        raise AppError(
            code=str(exc),
            message="This approval is no longer available",
            http_status=status.HTTP_409_CONFLICT,
        ) from None


@app.post("/fingerprint/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def fingerprint_cancel(req: FingerprintApprovalRequest):
    """Dismiss a pending fingerprint approval without charging."""
    await approvals.clear(req.approval_id, reason="cancelled")
    return None


@app.post("/dev/simulate/cash", response_model=SimulateCashResponse, include_in_schema=False)
async def dev_simulate_cash(req: SimulateCashRequest, db: AsyncSession = Depends(get_db)):
    """Simulate cash insertion in development mode."""
    if settings.environment != "dev":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    granted, remaining_or_accumulated = await process_cash_inserted(
        req.amount_cents,
        db,
        cash_session=cash_session,
        hardware_client=hardware_client,
        publish=_publish,
    )
    return SimulateCashResponse(
        granted=granted,
        accumulated_cents=cash_session.accumulated_cents,
        entrance_fee_cents=settings.entrance_fee_cents,
        remaining_cents=remaining_or_accumulated if granted else 0,
    )


@app.post("/management/auth", response_model=ManagementAuthResponse, include_in_schema=False)
async def management_auth(req: ManagementPinRequest, response: Response):
    """Authenticate the management PIN and set an HttpOnly session cookie."""
    return await authenticate_pin(req, response)


@app.get("/management/session", response_model=ManagementSessionResponse, include_in_schema=False)
async def management_session(request: Request):
    """Return whether the current request has a valid management session."""
    return get_management_session(request)


@app.post("/management/logout", response_model=ManagementSessionResponse, include_in_schema=False)
async def management_logout(request: Request, response: Response):
    """Revoke the management session and clear the HttpOnly cookie."""
    return await logout_management(request, response)


@app.get(
    "/management/chip/{uid}",
    response_model=MemberInfoResponse,
  include_in_schema=False,
)
async def management_chip_info(uid: str):
    """Return member balance and status for desk top-up / management UI."""
    return await get_chip_info(uid, member_client)


@app.post(
    "/management/chip/topup",
    response_model=MemberTopupResponse,
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_chip_topup(req: MemberTopupRequest):
    """Top up a member balance from the management panel."""
    return await topup_chip(req, member_client)


@app.get(
    "/management/users",
    response_model=list[ManagementUserResponse],
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_users_list():
    """List registered fingerprint ledger users."""
    return await management_list_users(member_client)


@app.patch(
    "/management/users/{member_id}",
    response_model=ManagementUserResponse,
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_users_update(member_id: str, req: ManagementUserUpdateRequest):
    """Edit a registered user's name and/or enabled flag."""
    return await management_update_user(member_id, req, member_client)


@app.delete(
    "/management/users/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_users_delete(member_id: str):
    """Delete a registered user and clear their fingerprint template when present."""
    await management_delete_user(member_id, member_client, hardware_client)
    return None


@app.post(
    "/management/cash-receipts/redeem",
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_redeem_cash_receipt(body: dict, db: AsyncSession = Depends(get_db)):
    """Mark a cash compensation receipt as paid out from the till."""
    if orchestrator is None:
        raise AppError(code="saga_unavailable", message="Saga not started", http_status=503)
    code = str(body.get("redeem_code", "")).strip()
    staff_id = str(body.get("staff_id") or "management")
    try:
        receipt = await orchestrator.redeem_receipt(code, staff_id, db)
    except ValueError as exc:
        raise AppError(code=str(exc), message=str(exc), http_status=409) from None
    return {
        "receipt_id": str(receipt.id),
        "attempt_id": str(receipt.attempt_id),
        "amount_cents": receipt.amount_cents,
        "status": receipt.status,
        "redeemed_at": receipt.redeemed_at.isoformat() if receipt.redeemed_at else None,
    }


@app.post(
    "/management/access-attempts/{attempt_id}/resolve",
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_resolve_attempt(attempt_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    """Staff resolves a MANUAL_REVIEW attempt after handling compensation offline."""
    if orchestrator is None:
        raise AppError(code="saga_unavailable", message="Saga not started", http_status=503)
    note = str(body.get("note") or "resolved")
    try:
        attempt = await orchestrator.resolve_manual_review(uuid.UUID(attempt_id), note, db)
    except ValueError as exc:
        raise AppError(code=str(exc), message=str(exc), http_status=409) from None
    return {"attempt_id": str(attempt.id), "status": attempt.status}


@app.post(
    "/management/door/open",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_door_open():
    """Manually open the door from the management panel."""
    await management_open_door(hardware_client)
    return None


@app.post(
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

    session = enrollments.create(
        holder_name=req.holder_name.strip(),
        national_id=national_id,
        initial_amount_cents=req.initial_amount_cents,
    )
    try:
        await hardware_client.enroll_fingerprint(session.session_id)
    except Exception:
        enrollments.discard(session.session_id)
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


@app.post(
    "/management/fingerprint/enroll/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
)
async def management_fingerprint_enroll_cancel(req: FingerprintEnrollCancelRequest):
    """Abort a running enrollment and forget its pending details."""
    enrollments.discard(req.session_id)
    await hardware_client.cancel_enroll()
    return None


@app.post(
    "/management/fingerprint/identify/start",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
)
async def management_fingerprint_identify_start():
    """Listen for fingerprint scans without creating door approvals."""
    await identify_sessions.start()
    return None


@app.post(
    "/management/fingerprint/identify/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
)
async def management_fingerprint_identify_cancel():
    """Stop desk identify mode and restore entrance scan behavior."""
    await identify_sessions.cancel()
    return None


@app.post("/hardware/events", status_code=status.HTTP_204_NO_CONTENT)
async def ingest_hardware_event(event: dict, db: AsyncSession = Depends(get_db)):
    """Persist a raw hardware event payload for auditing."""
    db.add(HardwareEvent(event_type=str(event.get("type", "unknown")), payload_json=json.dumps(event)))
    await db.commit()
    return None


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    """Stream live gate events to the dashboard over WebSocket."""
    await ws.accept()
    await fanout.register(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        fanout.unregister(ws)
    except Exception:
        fanout.unregister(ws)
        await ws.close()
