import json
import logging

import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from gate_shared.errors import AppError, ErrorResponse
from gate_shared.logging import configure_logging

from .provider import charge_credit_card
from .schemas import ChargeChipRequest, ChargeChipResponse
from .settings import settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Payment Service",
    version="0.1.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

redis_client: redis.Redis | None = None


@app.on_event("startup")
async def startup() -> None:
    global redis_client
    configure_logging(settings.service_name, settings.log_level)
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    logger.info("startup_complete")


@app.on_event("shutdown")
async def shutdown() -> None:
    global redis_client
    if redis_client is not None:
        await redis_client.aclose()
        redis_client = None


@app.exception_handler(AppError)
async def app_error_handler(_, exc: AppError):
    return JSONResponse(
        status_code=exc.http_status,
        content=ErrorResponse(code=exc.code, message=exc.message, details=exc.details).model_dump(),
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": settings.service_name}


@app.post("/charge-chip", response_model=ChargeChipResponse)
async def charge_chip(req: ChargeChipRequest):
    charge_credit_card(amount=req.amount)
    if redis_client is not None:
        await redis_client.publish(
            "payment.events",
            json.dumps({"type": "chip.charged", "amount": req.amount}),
        )
    logger.info("chip_charged amount=%s", req.amount)
    return ChargeChipResponse()
