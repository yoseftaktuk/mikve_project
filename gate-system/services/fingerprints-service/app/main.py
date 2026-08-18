import json
import logging
import uuid

import redis.asyncio as redis
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select

from gate_shared.errors import AppError, ErrorResponse
from gate_shared.logging import configure_logging

from .db import engine, get_db
from .models import Balance, Base, Member, MemberActivity, MonthlySubscription
from .schemas import (
    ActivateSubscriptionRequest,
    AdjustBalanceRequest,
    BalanceResponse,
    MemberActivityResponse,
    MemberAssignRequest,
    MemberCreateRequest,
    MemberListItemResponse,
    MemberRenameRequest,
    MemberResponse,
    MemberUpdateRequest,
    LookupByNationalIdRequest,
    LookupByNationalIdResponse,
    MarkFreeEntryRequest,
    SubscriptionResponse,
    ValidateMemberRequest,
    ValidateMemberResponse,
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
        # Rename tables from old chip-era names if they still exist.
        await conn.exec_driver_sql(
            f"""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = '{settings.postgres_schema}' AND table_name = 'chips'
              ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = '{settings.postgres_schema}' AND table_name = 'members'
              ) THEN
                EXECUTE 'ALTER TABLE {settings.postgres_schema}.chips RENAME TO members';
              END IF;
            END $$;
            """
        )
        await conn.exec_driver_sql(
            f"""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = '{settings.postgres_schema}' AND table_name = 'chip_activity'
              ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = '{settings.postgres_schema}' AND table_name = 'member_activity'
              ) THEN
                EXECUTE 'ALTER TABLE {settings.postgres_schema}.chip_activity RENAME TO member_activity';
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
                  AND table_name = 'balances'
                  AND column_name = 'chip_id'
              ) THEN
                EXECUTE 'ALTER TABLE {settings.postgres_schema}.balances RENAME COLUMN chip_id TO member_id';
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
                  AND table_name = 'member_activity'
                  AND column_name = 'chip_id'
              ) THEN
                EXECUTE 'ALTER TABLE {settings.postgres_schema}.member_activity RENAME COLUMN chip_id TO member_id';
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
                  AND table_name = 'monthly_subscriptions'
                  AND column_name = 'chip_id'
              ) THEN
                EXECUTE 'ALTER TABLE {settings.postgres_schema}.monthly_subscriptions RENAME COLUMN chip_id TO member_id';
              END IF;
            END $$;
            """
        )
        await conn.run_sync(Base.metadata.create_all)
        # The project has no migration tool, so databases created before holder_name
        # existed need this idempotent catch-up.
        await conn.exec_driver_sql(
            f"ALTER TABLE IF EXISTS {settings.postgres_schema}.members "
            "ADD COLUMN IF NOT EXISTS holder_name varchar(80)"
        )
        await conn.exec_driver_sql(
            f"ALTER TABLE IF EXISTS {settings.postgres_schema}.members "
            "ADD COLUMN IF NOT EXISTS national_id varchar(9)"
        )
        await conn.exec_driver_sql(
            f"CREATE UNIQUE INDEX IF NOT EXISTS members_national_id_key "
            f"ON {settings.postgres_schema}.members (national_id)"
        )
        await conn.exec_driver_sql(
            f"ALTER TABLE IF EXISTS {settings.postgres_schema}.member_activity "
            "ADD COLUMN IF NOT EXISTS idempotency_key varchar(80)"
        )
        await conn.exec_driver_sql(
            f"CREATE UNIQUE INDEX IF NOT EXISTS member_activity_idempotency_key_key "
            f"ON {settings.postgres_schema}.member_activity (idempotency_key)"
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
    """Publish a member event to Redis pub/sub."""
    if redis_client is None:
        return
    await redis_client.publish("member.events", json.dumps(event))


async def _ensure_national_id_available(
    db, national_id: str | None, *, exclude_member_id: uuid.UUID | None = None
) -> None:
    """Raise if national_id is already used by another member."""
    if not national_id:
        return
    query = select(Member).where(Member.national_id == national_id)
    if exclude_member_id is not None:
        query = query.where(Member.id != exclude_member_id)
    existing = await db.scalar(query)
    if existing:
        raise AppError(
            code="national_id_taken",
            message="National ID already registered",
            http_status=409,
        )


@app.post("/fingerprints", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def create_member(req: MemberCreateRequest, db=Depends(get_db)):
    """Register a new member UID with a zero balance."""
    existing = await db.scalar(select(Member).where(Member.uid == req.uid))
    if existing:
        raise AppError(code="fingerprint_uid_taken", message="Fingerprint UID already registered", http_status=400)
    await _ensure_national_id_available(db, req.national_id)
    member = Member(
        uid=req.uid,
        holder_name=req.holder_name,
        national_id=req.national_id,
        is_enabled=True,
    )
    db.add(member)
    await db.flush()
    db.add(Balance(member_id=member.id, amount_cents=0))
    db.add(MemberActivity(member_id=member.id, event_type="register", delta_cents=0, description="member registered"))
    await db.commit()
    await db.refresh(member)
    await _publish(
        {
            "type": "member.registered",
            "member_id": str(member.id),
            "uid": member.uid,
            "holder_name": member.holder_name,
            "national_id": member.national_id,
        }
    )
    return MemberResponse.model_validate(member, from_attributes=True)


@app.get("/fingerprints", response_model=list[MemberListItemResponse])
async def list_members(db=Depends(get_db)):
    """List registered members with balances, newest first."""
    rows = (
        await db.execute(
            select(Member, Balance.amount_cents)
            .outerjoin(Balance, Balance.member_id == Member.id)
            .order_by(Member.created_at.desc())
        )
    ).all()
    items: list[MemberListItemResponse] = []
    for member, amount_cents in rows:
        items.append(
            MemberListItemResponse(
                id=member.id,
                uid=member.uid,
                holder_name=member.holder_name,
                national_id=member.national_id,
                is_enabled=member.is_enabled,
                balance_cents=int(amount_cents or 0),
                created_at=member.created_at,
            )
        )
    return items


@app.get("/fingerprints/{member_id}", response_model=MemberResponse)
async def get_member(member_id: str, db=Depends(get_db)):
    """Return member metadata by internal member ID."""
    member = await db.get(Member, member_id)
    if not member:
        raise HTTPException(status_code=404, detail="member_not_found")
    return MemberResponse.model_validate(member, from_attributes=True)


@app.patch("/fingerprints/{member_id}", response_model=MemberListItemResponse)
async def update_member(member_id: str, req: MemberUpdateRequest, db=Depends(get_db)):
    """Update holder name, national ID, and/or enabled flag for a registered member."""
    fields = req.model_fields_set
    if "holder_name" not in fields and "national_id" not in fields and "is_enabled" not in fields:
        raise AppError(
            code="no_fields",
            message="Provide holder_name, national_id, and/or is_enabled",
            http_status=400,
        )
    member = await db.get(Member, member_id)
    if not member:
        raise HTTPException(status_code=404, detail="member_not_found")

    if "holder_name" in fields:
        holder_name = req.holder_name.strip() if req.holder_name else None
        member.holder_name = holder_name or None
        db.add(
            MemberActivity(
                member_id=member.id,
                event_type="rename",
                delta_cents=0,
                description=f"holder_name={member.holder_name or ''}",
            )
        )

    if "national_id" in fields:
        await _ensure_national_id_available(db, req.national_id, exclude_member_id=member.id)
        member.national_id = req.national_id
        db.add(
            MemberActivity(
                member_id=member.id,
                event_type="national_id",
                delta_cents=0,
                description=f"national_id={member.national_id or ''}",
            )
        )

    if "is_enabled" in fields and req.is_enabled is not None and req.is_enabled != member.is_enabled:
        member.is_enabled = req.is_enabled
        db.add(
            MemberActivity(
                member_id=member.id,
                event_type="enable" if member.is_enabled else "disable",
                delta_cents=0,
                description=f"is_enabled={member.is_enabled}",
            )
        )

    await db.commit()
    await db.refresh(member)
    bal = await db.get(Balance, member.id)
    return MemberListItemResponse(
        id=member.id,
        uid=member.uid,
        holder_name=member.holder_name,
        national_id=member.national_id,
        is_enabled=member.is_enabled,
        balance_cents=int(bal.amount_cents if bal else 0),
        created_at=member.created_at,
    )


@app.delete("/fingerprints/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(member_id: str, db=Depends(get_db)):
    """Remove a member, their balance, and activity history."""
    member = await db.get(Member, member_id)
    if not member:
        raise HTTPException(status_code=404, detail="member_not_found")
    await db.execute(delete(MonthlySubscription).where(MonthlySubscription.member_id == member.id))
    await db.execute(delete(MemberActivity).where(MemberActivity.member_id == member.id))
    await db.execute(delete(Balance).where(Balance.member_id == member.id))
    await db.delete(member)
    await db.commit()
    await _publish({"type": "member.deleted", "member_id": member_id, "uid": member.uid})
    return None


@app.patch("/fingerprints/{member_id}/assign", response_model=MemberResponse)
async def assign_member(member_id: str, req: MemberAssignRequest, db=Depends(get_db)):
    """Assign a member to a user ID."""
    member = await db.get(Member, member_id)
    if not member:
        raise HTTPException(status_code=404, detail="member_not_found")
    member.assigned_user_id = req.user_id
    db.add(MemberActivity(member_id=member.id, event_type="assign", delta_cents=0, description=f"assigned_user_id={req.user_id}"))
    await db.commit()
    await db.refresh(member)
    return MemberResponse.model_validate(member, from_attributes=True)


@app.patch("/fingerprints/{member_id}/name", response_model=MemberResponse)
async def rename_member(member_id: str, req: MemberRenameRequest, db=Depends(get_db)):
    """Set or clear the holder name and optional national ID."""
    member = await db.get(Member, member_id)
    if not member:
        raise HTTPException(status_code=404, detail="member_not_found")
    holder_name = req.holder_name.strip() if req.holder_name else None
    member.holder_name = holder_name or None
    if "national_id" in req.model_fields_set:
        await _ensure_national_id_available(db, req.national_id, exclude_member_id=member.id)
        member.national_id = req.national_id
    db.add(
        MemberActivity(
            member_id=member.id,
            event_type="rename",
            delta_cents=0,
            description=f"holder_name={member.holder_name or ''};national_id={member.national_id or ''}",
        )
    )
    await db.commit()
    await db.refresh(member)
    return MemberResponse.model_validate(member, from_attributes=True)


@app.get("/fingerprints/{member_id}/balance", response_model=BalanceResponse)
async def get_balance(member_id: str, db=Depends(get_db)):
    """Return the current balance for a member."""
    bal = await db.get(Balance, member_id)
    if not bal:
        raise HTTPException(status_code=404, detail="balance_not_found")
    return BalanceResponse.model_validate(bal, from_attributes=True)


@app.post("/fingerprints/{member_id}/balance/adjust", response_model=BalanceResponse)
async def adjust_balance(member_id: str, req: AdjustBalanceRequest, db=Depends(get_db)):
    """Apply a positive or negative balance delta and record activity.

    When idempotency_key is set and already recorded, return the current balance
    without applying the delta again (safe retries after a crash).
    """
    bal = await db.get(Balance, member_id)
    if not bal:
        raise HTTPException(status_code=404, detail="balance_not_found")

    key = req.idempotency_key.strip() if req.idempotency_key else None
    if key:
        existing = await db.scalar(select(MemberActivity).where(MemberActivity.idempotency_key == key))
        if existing is not None:
            return BalanceResponse.model_validate(bal, from_attributes=True)

    new_amount = bal.amount_cents + req.delta_cents
    if new_amount < 0:
        raise AppError(code="insufficient_balance", message="Balance cannot go below zero", http_status=409)
    bal.amount_cents = new_amount
    db.add(
        MemberActivity(
            member_id=bal.member_id,
            event_type=req.reason,
            delta_cents=req.delta_cents,
            description=req.description,
            idempotency_key=key,
        )
    )
    await db.commit()
    await db.refresh(bal)
    await _publish({"type": "member.balance_changed", "member_id": str(bal.member_id), "delta_cents": req.delta_cents})
    return BalanceResponse.model_validate(bal, from_attributes=True)


@app.post("/fingerprints/lookup-by-national-id", response_model=LookupByNationalIdResponse)
async def lookup_by_national_id(req: LookupByNationalIdRequest, db=Depends(get_db)):
    """Find a member by national ID (Nedarim Zeout). Do not pick among duplicates."""
    rows = (
        await db.execute(select(Member).where(Member.national_id == req.national_id))
    ).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="member_not_found")
    if len(rows) > 1:
        raise AppError(
            code="national_id_ambiguous",
            message="Multiple members share this national ID",
            http_status=409,
        )
    member = rows[0]
    bal = await db.get(Balance, member.id)
    if not bal:
        raise HTTPException(status_code=500, detail="balance_missing")
    return LookupByNationalIdResponse(
        member_id=member.id,
        uid=member.uid,
        is_enabled=member.is_enabled,
        balance_cents=bal.amount_cents,
        national_id=member.national_id,
    )


@app.post("/fingerprints/validate", response_model=ValidateMemberResponse)
async def validate(req: ValidateMemberRequest, db=Depends(get_db)):
    """Look up a member by UID and return enablement plus balance."""
    member = await db.scalar(select(Member).where(Member.uid == req.uid))
    if not member:
        raise HTTPException(status_code=404, detail="member_not_found")
    bal = await db.get(Balance, member.id)
    if not bal:
        raise HTTPException(status_code=500, detail="balance_missing")
    snap = await subscription_snapshot(db, member.id)
    db.add(MemberActivity(member_id=member.id, event_type="validate", delta_cents=0, description="member validated"))
    await db.commit()
    return ValidateMemberResponse(
        member_id=member.id,
        uid=member.uid,
        holder_name=member.holder_name,
        national_id=member.national_id,
        is_enabled=member.is_enabled,
        assigned_user_id=member.assigned_user_id,
        balance_cents=bal.amount_cents,
        subscription_active=snap.subscription_active,
        subscription_month_name=snap.subscription_month_name,
        subscription_free_entry_available_today=snap.subscription_free_entry_available_today,
        current_hebrew_month_name=snap.current_hebrew_month_name,
    )


def _subscription_response(row: MonthlySubscription, snap) -> SubscriptionResponse:
    return SubscriptionResponse(
        member_id=row.member_id,
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


@app.post("/fingerprints/{member_id}/subscriptions/activate", response_model=SubscriptionResponse)
async def subscriptions_activate(
    member_id: str, req: ActivateSubscriptionRequest, db=Depends(get_db)
):
    """Activate a Hebrew-month subscription after a successful card payment."""
    row = await activate_subscription(
        db,
        member_id=uuid.UUID(member_id),
        amount_cents=req.amount_cents,
        nedarim_transaction_id=req.nedarim_transaction_id,
        hebrew_year=req.hebrew_year,
        hebrew_month=req.hebrew_month,
        hebrew_month_name=req.hebrew_month_name,
    )
    snap = await subscription_snapshot(db, row.member_id)
    return _subscription_response(row, snap)


@app.post("/fingerprints/{member_id}/subscriptions/mark-free-entry", response_model=SubscriptionResponse)
async def subscriptions_mark_free_entry(
    member_id: str,
    req: MarkFreeEntryRequest | None = None,
    db=Depends(get_db),
):
    """Mark today's free subscription entrance as used."""
    row = await mark_free_entry(
        db,
        member_id=uuid.UUID(member_id),
        idempotency_key=req.idempotency_key if req else None,
    )
    snap = await subscription_snapshot(db, row.member_id)
    return _subscription_response(row, snap)


@app.get("/fingerprints/{member_id}/activity", response_model=list[MemberActivityResponse])
async def activity(member_id: str, db=Depends(get_db)):
    """Return member activity history newest first."""
    rows = (await db.execute(select(MemberActivity).where(MemberActivity.member_id == member_id).order_by(MemberActivity.id.desc()))).scalars().all()
    return [MemberActivityResponse.model_validate(r, from_attributes=True) for r in rows]
