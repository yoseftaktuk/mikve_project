import json
import logging
from datetime import datetime, timezone

import redis.asyncio as redis
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gate_shared.errors import AppError, ErrorResponse
from gate_shared.logging import configure_logging

from .access_logic import CashSession, process_cash_inserted, process_chip_access
from .clients import ChipClient, HardwareClient
from .db import engine, get_db
from .dev_helpers import DEMO_CHIP_UID, ensure_demo_chip
from .hardware_consumer import HardwareEventConsumer
from .models import AccessLog, Base, HardwareEvent
from .realtime import PubSubFanout
from .schemas import (
    AccessAttemptRequest,
    AccessDecisionResponse,
    AccessLogResponse,
    SimulateCashRequest,
    SimulateCashResponse,
)
from .management import (
    ChipInfoResponse,
    ChipTopupRequest,
    ChipTopupResponse,
    ManagementAuthResponse,
    ManagementPinRequest,
    authenticate_pin,
    get_chip_info,
    open_door as management_open_door,
    require_management_token,
    topup_chip,
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

chip_client = ChipClient()
hardware_client = HardwareClient()
cash_session = CashSession(timeout_seconds=settings.cash_session_timeout_seconds)
fanout = PubSubFanout(settings.redis_url)
hardware_consumer: HardwareEventConsumer | None = None
redis_client: redis.Redis | None = None


@app.on_event("startup")
async def startup() -> None:
    """Create tables, connect Redis, and start event consumers."""
    global redis_client, hardware_consumer
    configure_logging(settings.service_name, settings.log_level)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    cash_session.set_publish(_publish)
    await fanout.start()
    hardware_consumer = HardwareEventConsumer(
        settings.redis_url,
        chip_client=chip_client,
        hardware_client=hardware_client,
        cash_session=cash_session,
        publish=_publish,
    )
    await hardware_consumer.start()
    logger.info(
        "startup_complete entrance_fee_cents=%s door_unlock_seconds=%s cash_session_timeout_seconds=%s",
        settings.entrance_fee_cents,
        settings.door_unlock_seconds,
        settings.cash_session_timeout_seconds,
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    """Stop consumers and close Redis on service shutdown."""
    global redis_client, hardware_consumer
    await cash_session.shutdown()
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


@app.post("/access/attempt", response_model=AccessDecisionResponse)
async def access_attempt(req: AccessAttemptRequest, db: AsyncSession = Depends(get_db)):
    """Attempt entrance authorization for a scanned chip UID."""
    return await process_chip_access(
        req.uid,
        db,
        chip_client=chip_client,
        hardware_client=hardware_client,
        publish=_publish,
    )


@app.get("/access/logs", response_model=list[AccessLogResponse])
async def access_logs(db: AsyncSession = Depends(get_db), limit: int = 50):
    """Return recent access grant/deny log entries."""
    limit = max(1, min(limit, 200))
    rows = (await db.execute(select(AccessLog).order_by(AccessLog.id.desc()).limit(limit))).scalars().all()
    return [AccessLogResponse.model_validate(r, from_attributes=True) for r in rows]


@app.post("/dev/simulate/chip", response_model=AccessDecisionResponse, include_in_schema=False)
async def dev_simulate_chip(db: AsyncSession = Depends(get_db)):
    """Simulate a demo chip scan in development mode."""
    if settings.environment != "dev":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    await ensure_demo_chip(chip_client)
    return await process_chip_access(
        DEMO_CHIP_UID,
        db,
        chip_client=chip_client,
        hardware_client=hardware_client,
        publish=_publish,
    )


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
async def management_auth(req: ManagementPinRequest):
    """Authenticate the management PIN and return a session token."""
    return await authenticate_pin(req)


@app.get(
    "/management/chip/{uid}",
    response_model=ChipInfoResponse,
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_chip_info(uid: str):
    """Return chip balance and status for management UI."""
    return await get_chip_info(uid, chip_client)


@app.post(
    "/management/chip/topup",
    response_model=ChipTopupResponse,
    dependencies=[Depends(require_management_token)],
    include_in_schema=False,
)
async def management_chip_topup(req: ChipTopupRequest):
    """Top up a chip balance from the management panel."""
    return await topup_chip(req, chip_client)


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
