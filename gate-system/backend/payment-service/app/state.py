"""Shared runtime objects for payment startup and routes."""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis

from .clients import FingerprintsClient
from .payment_provider import PaymentProvider, build_payment_provider

redis_client: redis.Redis | None = None
member_client = FingerprintsClient()
payment_provider: PaymentProvider = build_payment_provider()


async def publish_payment_event(event: dict[str, Any]) -> None:
    if redis_client is None:
        return
    await redis_client.publish("payment.events", json.dumps(event))
