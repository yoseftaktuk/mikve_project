import json
import logging
import uuid

import redis.asyncio as redis
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from gate_shared.errors import AppError, ErrorResponse
from gate_shared.logging import configure_logging

from .db import engine, get_db
from .models import Balance, Base, Chip, ChipActivity, MonthlySubscription
from .schemas import (
    ActivateSubscriptionRequest,
    AdjustBalanceRequest,
    BalanceResponse,
    ChipActivityResponse,
    ChipAssignRequest,
    ChipCreateRequest,
    ChipListItemResponse,
    ChipRenameRequest,
    ChipResponse,
    ChipUpdateRequest,
    LookupByNationalIdRequest,
    LookupByNationalIdResponse,
    MarkFreeEntryRequest,
    SubscriptionResponse,
    ValidateChipRequest,
    ValidateChipResponse,
)
from .settings import settings
from .subscription_logic import activate_subscription, mark_free_entry, subscription_snapshot

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Fingerprints Service",
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
        # Existing volumes may still have chip_service from before the rename.
        await conn.exec_driver_sql(
            f"""
            DO $$
            BEGIN
              IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'chip_service')
                 AND NOT EXISTS (
                   SELECT 1 FROM information_schema.schemata WHERE schema_name = '{settings.postgres_schema}'
                 ) THEN
                EXECUTE 'ALTER SCHEMA chip_service RENAME TO {settings.postgres_schema}';
              END IF;
            END $$;
            """
        )
        await conn.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {settings.postgres_schema}")
        await conn.run_sync(Base.metadata.create_all)
        # The project has no migration tool, so databases created before holder_name
        # existed need this idempotent catch-up.
        await conn.exec_driver_sql(
            f"ALTER TABLE IF EXISTS {settings.postgres_schema}.chips "
            "ADD COLUMN IF NOT EXISTS holder_name varchar(80)"
        )
        await conn.exec_driver_sql(
            f"ALTER TABLE IF EXISTS {settings.postgres_schema}.chips "
            "ADD COLUMN IF NOT EXISTS national_id varchar(9)"
        )
        await conn.exec_driver_sql(
            f"CREATE UNIQUE INDEX IF NOT EXISTS chips_national_id_key "
            f"ON {settings.postgres_schema}.chips (national_id)"
        )
        await conn.exec_driver_sql(
            f"ALTER TABLE IF EXISTS {settings.postgres_schema}.chip_activity "
            "ADD COLUMN IF NOT EXISTS idempotency_key varchar(80)"
        )
        await conn.exec_driver_sql(
            f"CREATE UNIQUE INDEX IF NOT EXISTS chip_activity_idempotency_key_key "
            f"ON {settings.postgres_schema}.chip_activity (idempotency_key)"
        )
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


async def _ensure_national_id_available(
    db: AsyncSession, national_id: str | None, *, exclude_chip_id: uuid.UUID | None = None
) -> None:
    """Raise if national_id is already used by another chip."""
    if not national_id:
        return
    query = select(Chip).where(Chip.national_id == national_id)
    if exclude_chip_id is not None:
        query = query.where(Chip.id != exclude_chip_id)
    existing = await db.scalar(query)
    if existing:
        raise AppError(
            code="national_id_taken",
            message="National ID already registered",
            http_status=409,
        )


@app.post("/fingerprints", response_model=ChipResponse, status_code=status.HTTP_201_CREATED)
async def create_chip(req: ChipCreateRequest, db: AsyncSession = Depends(get_db)):
    """Register a new chip UID with a zero balance."""
    existing = await db.scalar(select(Chip).where(Chip.uid == req.uid))
    if existing:
        raise AppError(code="fingerprint_uid_taken", message="Fingerprint UID already registered", http_status=400)
    await _ensure_national_id_available(db, req.national_id)
    chip = Chip(
        uid=req.uid,
        holder_name=req.holder_name,
        national_id=req.national_id,
        is_enabled=True,
    )
    db.add(chip)
    await db.flush()
    db.add(Balance(chip_id=chip.id, amount_cents=0))
    db.add(ChipActivity(chip_id=chip.id, event_type="register", delta_cents=0, description="chip registered"))
    await db.commit()
    await db.refresh(chip)
    await _publish(
        {
            "type": "chip.registered",
            "chip_id": str(chip.id),
            "uid": chip.uid,
            "holder_name": chip.holder_name,
            "national_id": chip.national_id,
        }
    )
    return ChipResponse.model_validate(chip, from_attributes=True)


@app.get("/fingerprints", response_model=list[ChipListItemResponse])
async def list_chips(db: AsyncSession = Depends(get_db)):
    """List registered chips with balances, newest first."""
    rows = (
        await db.execute(
            select(Chip, Balance.amount_cents)
            .outerjoin(Balance, Balance.chip_id == Chip.id)
            .order_by(Chip.created_at.desc())
        )
    ).all()
    items: list[ChipListItemResponse] = []
    for chip, amount_cents in rows:
        items.append(
            ChipListItemResponse(
                id=chip.id,
                uid=chip.uid,
                holder_name=chip.holder_name,
                national_id=chip.national_id,
                is_enabled=chip.is_enabled,
                balance_cents=int(amount_cents or 0),
                created_at=chip.created_at,
            )
        )
    return items


@app.get("/fingerprints/{chip_id}", response_model=ChipResponse)
async def get_chip(chip_id: str, db: AsyncSession = Depends(get_db)):
    """Return chip metadata by internal chip ID."""
    chip = await db.get(Chip, chip_id)
    if not chip:
        raise HTTPException(status_code=404, detail="chip_not_found")
    return ChipResponse.model_validate(chip, from_attributes=True)


@app.patch("/fingerprints/{chip_id}", response_model=ChipListItemResponse)
async def update_chip(chip_id: str, req: ChipUpdateRequest, db: AsyncSession = Depends(get_db)):
    """Update holder name, national ID, and/or enabled flag for a registered chip."""
    fields = req.model_fields_set
    if "holder_name" not in fields and "national_id" not in fields and "is_enabled" not in fields:
        raise AppError(
            code="no_fields",
            message="Provide holder_name, national_id, and/or is_enabled",
            http_status=400,
        )
    chip = await db.get(Chip, chip_id)
    if not chip:
        raise HTTPException(status_code=404, detail="chip_not_found")

    if "holder_name" in fields:
        holder_name = req.holder_name.strip() if req.holder_name else None
        chip.holder_name = holder_name or None
        db.add(
            ChipActivity(
                chip_id=chip.id,
                event_type="rename",
                delta_cents=0,
                description=f"holder_name={chip.holder_name or ''}",
            )
        )

    if "national_id" in fields:
        await _ensure_national_id_available(db, req.national_id, exclude_chip_id=chip.id)
        chip.national_id = req.national_id
        db.add(
            ChipActivity(
                chip_id=chip.id,
                event_type="national_id",
                delta_cents=0,
                description=f"national_id={chip.national_id or ''}",
            )
        )

    if "is_enabled" in fields and req.is_enabled is not None and req.is_enabled != chip.is_enabled:
        chip.is_enabled = req.is_enabled
        db.add(
            ChipActivity(
                chip_id=chip.id,
                event_type="enable" if chip.is_enabled else "disable",
                delta_cents=0,
                description=f"is_enabled={chip.is_enabled}",
            )
        )

    await db.commit()
    await db.refresh(chip)
    bal = await db.get(Balance, chip.id)
    return ChipListItemResponse(
        id=chip.id,
        uid=chip.uid,
        holder_name=chip.holder_name,
        national_id=chip.national_id,
        is_enabled=chip.is_enabled,
        balance_cents=int(bal.amount_cents if bal else 0),
        created_at=chip.created_at,
    )


@app.delete("/fingerprints/{chip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chip(chip_id: str, db: AsyncSession = Depends(get_db)):
    """Remove a chip, its balance, and activity history."""
    chip = await db.get(Chip, chip_id)
    if not chip:
        raise HTTPException(status_code=404, detail="chip_not_found")
    await db.execute(delete(MonthlySubscription).where(MonthlySubscription.chip_id == chip.id))
    await db.execute(delete(ChipActivity).where(ChipActivity.chip_id == chip.id))
    await db.execute(delete(Balance).where(Balance.chip_id == chip.id))
    await db.delete(chip)
    await db.commit()
    await _publish({"type": "chip.deleted", "chip_id": chip_id, "uid": chip.uid})
    return None


@app.patch("/fingerprints/{chip_id}/assign", response_model=ChipResponse)
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


@app.patch("/fingerprints/{chip_id}/name", response_model=ChipResponse)
async def rename_chip(chip_id: str, req: ChipRenameRequest, db: AsyncSession = Depends(get_db)):
    """Set or clear the holder name and optional national ID."""
    chip = await db.get(Chip, chip_id)
    if not chip:
        raise HTTPException(status_code=404, detail="chip_not_found")
    holder_name = req.holder_name.strip() if req.holder_name else None
    chip.holder_name = holder_name or None
    if "national_id" in req.model_fields_set:
        await _ensure_national_id_available(db, req.national_id, exclude_chip_id=chip.id)
        chip.national_id = req.national_id
    db.add(
        ChipActivity(
            chip_id=chip.id,
            event_type="rename",
            delta_cents=0,
            description=f"holder_name={chip.holder_name or ''};national_id={chip.national_id or ''}",
        )
    )
    await db.commit()
    await db.refresh(chip)
    return ChipResponse.model_validate(chip, from_attributes=True)


@app.get("/fingerprints/{chip_id}/balance", response_model=BalanceResponse)
async def get_balance(chip_id: str, db: AsyncSession = Depends(get_db)):
    """Return the current balance for a chip."""
    bal = await db.get(Balance, chip_id)
    if not bal:
        raise HTTPException(status_code=404, detail="balance_not_found")
    return BalanceResponse.model_validate(bal, from_attributes=True)


@app.post("/fingerprints/{chip_id}/balance/adjust", response_model=BalanceResponse)
async def adjust_balance(chip_id: str, req: AdjustBalanceRequest, db: AsyncSession = Depends(get_db)):
    """Apply a positive or negative balance delta and record activity.

    When idempotency_key is set and already recorded, return the current balance
    without applying the delta again (safe retries after a crash).
    """
    bal = await db.get(Balance, chip_id)
    if not bal:
        raise HTTPException(status_code=404, detail="balance_not_found")

    key = req.idempotency_key.strip() if req.idempotency_key else None
    if key:
        existing = await db.scalar(select(ChipActivity).where(ChipActivity.idempotency_key == key))
        if existing is not None:
            return BalanceResponse.model_validate(bal, from_attributes=True)

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
            idempotency_key=key,
        )
    )
    await db.commit()
    await db.refresh(bal)
    await _publish({"type": "chip.balance_changed", "chip_id": str(bal.chip_id), "delta_cents": req.delta_cents})
    return BalanceResponse.model_validate(bal, from_attributes=True)


@app.post("/fingerprints/lookup-by-national-id", response_model=LookupByNationalIdResponse)
async def lookup_by_national_id(req: LookupByNationalIdRequest, db: AsyncSession = Depends(get_db)):
    """Find a chip by national ID (Nedarim Zeout). Do not pick among duplicates."""
    rows = (
        await db.execute(select(Chip).where(Chip.national_id == req.national_id))
    ).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="chip_not_found")
    if len(rows) > 1:
        raise AppError(
            code="national_id_ambiguous",
            message="Multiple chips share this national ID",
            http_status=409,
        )
    chip = rows[0]
    bal = await db.get(Balance, chip.id)
    if not bal:
        raise HTTPException(status_code=500, detail="balance_missing")
    return LookupByNationalIdResponse(
        chip_id=chip.id,
        uid=chip.uid,
        is_enabled=chip.is_enabled,
        balance_cents=bal.amount_cents,
        national_id=chip.national_id,
    )


@app.post("/fingerprints/validate", response_model=ValidateChipResponse)
async def validate(req: ValidateChipRequest, db: AsyncSession = Depends(get_db)):
    """Look up a chip by UID and return enablement plus balance."""
    chip = await db.scalar(select(Chip).where(Chip.uid == req.uid))
    if not chip:
        raise HTTPException(status_code=404, detail="chip_not_found")
    bal = await db.get(Balance, chip.id)
    if not bal:
        raise HTTPException(status_code=500, detail="balance_missing")
    snap = await subscription_snapshot(db, chip.id)
    db.add(ChipActivity(chip_id=chip.id, event_type="validate", delta_cents=0, description="chip validated"))
    await db.commit()
    return ValidateChipResponse(
        chip_id=chip.id,
        uid=chip.uid,
        holder_name=chip.holder_name,
        national_id=chip.national_id,
        is_enabled=chip.is_enabled,
        assigned_user_id=chip.assigned_user_id,
        balance_cents=bal.amount_cents,
        subscription_active=snap.subscription_active,
        subscription_month_name=snap.subscription_month_name,
        subscription_free_entry_available_today=snap.subscription_free_entry_available_today,
        current_hebrew_month_name=snap.current_hebrew_month_name,
    )


def _subscription_response(row: MonthlySubscription, snap) -> SubscriptionResponse:
    return SubscriptionResponse(
        chip_id=row.chip_id,
        hebrew_year=row.hebrew_year,
        hebrew_month=row.hebrew_month,
        hebrew_month_name=row.hebrew_month_name,
        amount_cents=row.amount_cents,
        nedarim_transaction_id=row.nedarim_transaction_id,
        status=row.status,
        last_free_entry_on=row.last_free_entry_on,
        purchased_at=row.purchased_at,
        subscription_active=snap.subscription_active,
        subscription_free_entry_available_today=snap.subscription_free_entry_available_today,
        current_hebrew_month_name=snap.current_hebrew_month_name,
    )


@app.post("/fingerprints/{chip_id}/subscriptions/activate", response_model=SubscriptionResponse)
async def subscriptions_activate(
    chip_id: str, req: ActivateSubscriptionRequest, db: AsyncSession = Depends(get_db)
):
    """Activate a Hebrew-month subscription after a successful card payment."""
    row = await activate_subscription(
        db,
        chip_id=uuid.UUID(chip_id),
        amount_cents=req.amount_cents,
        nedarim_transaction_id=req.nedarim_transaction_id,
        hebrew_year=req.hebrew_year,
        hebrew_month=req.hebrew_month,
        hebrew_month_name=req.hebrew_month_name,
    )
    snap = await subscription_snapshot(db, row.chip_id)
    return _subscription_response(row, snap)


@app.post("/fingerprints/{chip_id}/subscriptions/mark-free-entry", response_model=SubscriptionResponse)
async def subscriptions_mark_free_entry(
    chip_id: str,
    req: MarkFreeEntryRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Mark today's free subscription entrance as used."""
    row = await mark_free_entry(
        db,
        chip_id=uuid.UUID(chip_id),
        idempotency_key=req.idempotency_key if req else None,
    )
    snap = await subscription_snapshot(db, row.chip_id)
    return _subscription_response(row, snap)


@app.get("/fingerprints/{chip_id}/activity", response_model=list[ChipActivityResponse])
async def activity(chip_id: str, db: AsyncSession = Depends(get_db)):
    """Return chip activity history newest first."""
    rows = (await db.execute(select(ChipActivity).where(ChipActivity.chip_id == chip_id).order_by(ChipActivity.id.desc()))).scalars().all()
    return [ChipActivityResponse.model_validate(r, from_attributes=True) for r in rows]
