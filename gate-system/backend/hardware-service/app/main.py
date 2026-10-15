"""Hardware service entrypoint: app lifecycle and adapter wiring."""

from __future__ import annotations

import logging

import redis.asyncio as redis
from fastapi import FastAPI

from gate_shared.logging import configure_logging

from .hardware import MockHardwareAdapter, RpiHardwareAdapter
from .routes import router
from .settings import settings
from . import state

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Hardware Service",
    version="0.1.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)
app.include_router(router)


@app.on_event("startup")
async def startup() -> None:
    """Connect Redis and start the mock or Raspberry Pi hardware adapter."""
    configure_logging(settings.service_name, settings.log_level)
    state.redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    if settings.hardware_mode == "mock":
        state.adapter = MockHardwareAdapter(
            on_rfid_scan=state.on_rfid_scan,
            on_cash_inserted=state.on_cash_inserted,
            on_fingerprint_scan=state.on_fingerprint_scan,
            on_fingerprint_unmatched=state.on_fingerprint_unmatched,
            on_fingerprint_progress=state.on_fingerprint_progress,
        )
    else:
        state.adapter = RpiHardwareAdapter(
            on_rfid_scan=state.on_rfid_scan,
            on_cash_inserted=state.on_cash_inserted,
            coin_pin=settings.coin_acceptor_gpio_pin,
            door_pin=settings.door_relay_gpio_pin,
            door_unlock_seconds=settings.door_unlock_seconds,
            rfid_serial_port=settings.rfid_serial_port,
            rfid_baudrate=settings.rfid_baudrate,
            door_relay_idle_high=settings.door_relay_idle_high,
            on_fingerprint_scan=state.on_fingerprint_scan,
            on_fingerprint_unmatched=state.on_fingerprint_unmatched,
            on_fingerprint_progress=state.on_fingerprint_progress,
            fingerprint_serial_port=settings.fingerprint_serial_port,
            fingerprint_baudrate=settings.fingerprint_baudrate,
        )
    await state.adapter.start()
    logger.info("startup_complete mode=%s", settings.hardware_mode)


@app.on_event("shutdown")
async def shutdown() -> None:
    """Stop the hardware adapter and close the Redis connection."""
    if state.adapter is not None:
        await state.adapter.stop()
        state.adapter = None
    if state.redis_client is not None:
        await state.redis_client.aclose()
        state.redis_client = None
