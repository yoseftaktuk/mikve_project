"""Access-control service entrypoint: app lifecycle and wiring."""

from __future__ import annotations

import logging

import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from gate_shared.errors import AppError, ErrorResponse
from gate_shared.logging import configure_logging

from .db import SessionLocal, engine
from .hardware_consumer import HardwareEventConsumer
from .models import Base
from .pricing import ensure_pricing_row
from .routes import router
from .saga import AccessAttemptReconciler, AccessOrchestrator
from .saga import models as saga_models  # noqa: F401 — register saga tables on Base.metadata
from .settings import settings
from . import state

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Access Control Service",
    version="0.1.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)
app.include_router(router)


@app.on_event("startup")
async def startup() -> None:
    """Create tables, connect Redis, and start event consumers."""
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
    async with SessionLocal() as session:
        pricing = await ensure_pricing_row(session)
        await session.commit()
        seeded_entrance = pricing.entrance_fee_cents
        seeded_subscription = pricing.subscription_price_cents
    state.redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    state.cash_session.set_publish(state.publish)
    state.approvals.set_publish(state.publish)
    state.orchestrator = AccessOrchestrator(
        member_client=state.member_client,
        hardware_client=state.hardware_client,
        cash_session=state.cash_session,
        publish=state.publish,
    )
    await state.fanout.start()
    state.hardware_consumer = HardwareEventConsumer(
        settings.redis_url,
        member_client=state.member_client,
        hardware_client=state.hardware_client,
        cash_session=state.cash_session,
        publish=state.publish,
        approvals=state.approvals,
        enrollments=state.enrollments,
        identify=state.identify_sessions,
    )
    await state.hardware_consumer.start()
    if settings.access_saga_enabled:
        state.reconciler = AccessAttemptReconciler(state.orchestrator)
        await state.reconciler.start()
    logger.info(
        "startup_complete entrance_fee_cents=%s subscription_price_cents=%s door_unlock_seconds=%s cash_session_timeout_seconds=%s access_saga_enabled=%s",
        seeded_entrance,
        seeded_subscription,
        settings.door_unlock_seconds,
        settings.cash_session_timeout_seconds,
        settings.access_saga_enabled,
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    """Stop consumers and close Redis on service shutdown."""
    if state.reconciler is not None:
        await state.reconciler.stop()
        state.reconciler = None
    await state.cash_session.shutdown()
    await state.approvals.shutdown()
    state.enrollments.clear()
    state.identify_sessions.clear()
    if state.hardware_consumer is not None:
        await state.hardware_consumer.stop()
        state.hardware_consumer = None
    await state.fanout.stop()
    if state.redis_client is not None:
        await state.redis_client.aclose()
        state.redis_client = None


@app.exception_handler(AppError)
async def app_error_handler(_, exc: AppError):
    """Convert AppError exceptions into JSON error responses."""
    return JSONResponse(
        status_code=exc.http_status,
        content=ErrorResponse(code=exc.code, message=exc.message, details=exc.details).model_dump(),
    )
