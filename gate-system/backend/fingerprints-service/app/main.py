"""Fingerprints service entrypoint: app lifecycle and wiring."""

from __future__ import annotations

import logging

import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from gate_shared.errors import AppError, ErrorResponse
from gate_shared.logging import configure_logging

from .db import engine
from .models import Base
from .routes import router
from .settings import settings
from . import state

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Fingerprints Service",
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
    state.redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    logger.info("startup_complete")


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
