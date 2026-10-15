"""Payment service entrypoint: app lifecycle and wiring."""

from __future__ import annotations

import logging

import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from gate_shared.errors import AppError, ErrorResponse
from gate_shared.logging import configure_logging

from .db import engine
from .models import Base
from .payment_provider import build_payment_provider
from .routes import router
from .settings import settings
from . import state

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Payment Service",
    version="0.1.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)
app.include_router(router)


@app.on_event("startup")
async def startup() -> None:
    """Create tables and connect to Redis."""
    configure_logging(settings.service_name, settings.log_level)
    if not settings.postgres_dsn:
        raise RuntimeError("POSTGRES_DSN is required for payment-service")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {settings.postgres_schema}")
        await conn.run_sync(Base.metadata.create_all)
        # Existing volumes may still have chip_uid from before the rename.
        await conn.exec_driver_sql(
            f"""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '{settings.postgres_schema}'
                  AND table_name = 'card_topups'
                  AND column_name = 'chip_uid'
              ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '{settings.postgres_schema}'
                  AND table_name = 'card_topups'
                  AND column_name = 'fingerprint_uid'
              ) THEN
                EXECUTE 'ALTER TABLE {settings.postgres_schema}.card_topups RENAME COLUMN chip_uid TO fingerprint_uid';
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
                  AND table_name = 'card_topups'
                  AND column_name = 'chip_id'
              ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '{settings.postgres_schema}'
                  AND table_name = 'card_topups'
                  AND column_name = 'member_id'
              ) THEN
                EXECUTE 'ALTER TABLE {settings.postgres_schema}.card_topups RENAME COLUMN chip_id TO member_id';
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
                  AND table_name = 'nedarim_webhook_events'
                  AND column_name = 'matched_chip_id'
              ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '{settings.postgres_schema}'
                  AND table_name = 'nedarim_webhook_events'
                  AND column_name = 'matched_member_id'
              ) THEN
                EXECUTE 'ALTER TABLE {settings.postgres_schema}.nedarim_webhook_events RENAME COLUMN matched_chip_id TO matched_member_id';
              END IF;
            END $$;
            """
        )
        await conn.exec_driver_sql(
            f"ALTER TABLE IF EXISTS {settings.postgres_schema}.card_topups "
            "ADD COLUMN IF NOT EXISTS product varchar(32) NOT NULL DEFAULT 'balance'"
        )
        await conn.exec_driver_sql(
            f"CREATE UNIQUE INDEX IF NOT EXISTS nedarim_webhook_events_transaction_id_key "
            f"ON {settings.postgres_schema}.nedarim_webhook_events (transaction_id)"
        )
    state.redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    state.payment_provider = build_payment_provider()
    logger.info(
        "startup_complete payment_mode=%s provider_ready=%s public_base_url_set=%s topup_amounts=%s",
        settings.payment_mode,
        state.payment_provider.is_configured,
        bool(settings.public_base_url),
        list(settings.topup_amount_options_cents),
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    """Close the Redis connection on shutdown."""
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
