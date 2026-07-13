import json
import logging

import redis.asyncio as redis
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gate_shared.errors import AppError, ErrorResponse
from gate_shared.logging import configure_logging

from .db import engine, get_db
from .models import Balance, Base, Chip, ChipActivity
from .schemas import (
    AdjustBalanceRequest,
    BalanceResponse,
    ChipActivityResponse,
    ChipAssignRequest,
    ChipCreateRequest,
    ChipResponse,
    ValidateChipRequest,
    ValidateChipResponse,
)
from .settings import settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Chip Service",
    version="0.1.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

redis_client: redis.Redis | None = None


@app.on_event("startup")
async def startup() -> None:
    """Create tables and connect to Redis."""
    global redis_client
    configure_logging(settings.service_name, settings.log_level)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    logger.info("startup_complete")


@app.on_event("shutdown")
async def shutdown() -> None:
    """Close the Redis connection on shutdown."""
    global redis_client
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
    return {"status": "ok", "service": settings.service_name}


async def _publish(event: dict) -> None:
    """Publish a chip event to Redis pub/sub."""
    if redis_client is None:
        return
    await redis_client.publish("chip.events", json.dumps(event))


@app.post("/chips", response_model=ChipResponse, status_code=status.HTTP_201_CREATED)
async def create_chip(req: ChipCreateRequest, db: AsyncSession = Depends(get_db)):
    """Register a new chip UID with a zero balance."""
    existing = await db.scalar(select(Chip).where(Chip.uid == req.uid))
    if existing:
        raise AppError(code="chip_uid_taken", message="Chip UID already registered", http_status=400)
    chip = Chip(uid=req.uid, is_enabled=True)
    db.add(chip)
    await db.flush()
    db.add(Balance(chip_id=chip.id, amount_cents=0))
    db.add(ChipActivity(chip_id=chip.id, event_type="register", delta_cents=0, description="chip registered"))
    await db.commit()
    await db.refresh(chip)
    await _publish({"type": "chip.registered", "chip_id": str(chip.id), "uid": chip.uid})
    return ChipResponse.model_validate(chip, from_attributes=True)


@app.get("/chips/{chip_id}", response_model=ChipResponse)
async def get_chip(chip_id: str, db: AsyncSession = Depends(get_db)):
    """Return chip metadata by internal chip ID."""
    chip = await db.get(Chip, chip_id)
    if not chip:
        raise HTTPException(status_code=404, detail="chip_not_found")
    return ChipResponse.model_validate(chip, from_attributes=True)


@app.patch("/chips/{chip_id}/assign", response_model=ChipResponse)
async def assign_chip(chip_id: str, req: ChipAssignRequest, db: AsyncSession = Depends(get_db)):
    """Assign a chip to a user ID."""
    chip = await db.get(Chip, chip_id)
    if not chip:
        raise HTTPException(status_code=404, detail="chip_not_found")
    chip.assigned_user_id = req.user_id
    db.add(ChipActivity(chip_id=chip.id, event_type="assign", delta_cents=0, description=f"assigned_user_id={req.user_id}"))
    await db.commit()
    await db.refresh(chip)
    return ChipResponse.model_validate(chip, from_attributes=True)


@app.get("/chips/{chip_id}/balance", response_model=BalanceResponse)
async def get_balance(chip_id: str, db: AsyncSession = Depends(get_db)):
    """Return the current balance for a chip."""
    bal = await db.get(Balance, chip_id)
    if not bal:
        raise HTTPException(status_code=404, detail="balance_not_found")
    return BalanceResponse.model_validate(bal, from_attributes=True)


@app.post("/chips/{chip_id}/balance/adjust", response_model=BalanceResponse)
async def adjust_balance(chip_id: str, req: AdjustBalanceRequest, db: AsyncSession = Depends(get_db)):
    """Apply a positive or negative balance delta and record activity."""
    bal = await db.get(Balance, chip_id)
    if not bal:
        raise HTTPException(status_code=404, detail="balance_not_found")
    new_amount = bal.amount_cents + req.delta_cents
    if new_amount < 0:
        raise AppError(code="insufficient_balance", message="Balance cannot go below zero", http_status=409)
    bal.amount_cents = new_amount
    db.add(
        ChipActivity(
            chip_id=bal.chip_id,
            event_type=req.reason,
            delta_cents=req.delta_cents,
            description=req.description,
        )
    )
    await db.commit()
    await db.refresh(bal)
    await _publish({"type": "chip.balance_changed", "chip_id": str(bal.chip_id), "delta_cents": req.delta_cents})
    return BalanceResponse.model_validate(bal, from_attributes=True)


@app.post("/chips/validate", response_model=ValidateChipResponse)
async def validate(req: ValidateChipRequest, db: AsyncSession = Depends(get_db)):
    """Look up a chip by UID and return enablement plus balance."""
    chip = await db.scalar(select(Chip).where(Chip.uid == req.uid))
    if not chip:
        raise HTTPException(status_code=404, detail="chip_not_found")
    bal = await db.get(Balance, chip.id)
    if not bal:
        raise HTTPException(status_code=500, detail="balance_missing")
    db.add(ChipActivity(chip_id=chip.id, event_type="validate", delta_cents=0, description="chip validated"))
    await db.commit()
    return ValidateChipResponse(
        chip_id=chip.id,
        uid=chip.uid,
        is_enabled=chip.is_enabled,
        assigned_user_id=chip.assigned_user_id,
        balance_cents=bal.amount_cents,
    )


@app.get("/chips/{chip_id}/activity", response_model=list[ChipActivityResponse])
async def activity(chip_id: str, db: AsyncSession = Depends(get_db)):
    """Return chip activity history newest first."""
    rows = (await db.execute(select(ChipActivity).where(ChipActivity.chip_id == chip_id).order_by(ChipActivity.id.desc()))).scalars().all()
    return [ChipActivityResponse.model_validate(r, from_attributes=True) for r in rows]
