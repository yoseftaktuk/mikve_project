from __future__ import annotations

import asyncio
import json
import logging

import redis.asyncio as redis

from .access_logic import CashSession, process_cash_inserted, process_chip_access
from .clients import ChipClient, HardwareClient
from .db import SessionLocal

logger = logging.getLogger(__name__)


class HardwareEventConsumer:
    """Subscribes to hardware.events and runs chip/cash access logic."""

    def __init__(
        self,
        redis_url: str,
        *,
        chip_client: ChipClient,
        hardware_client: HardwareClient,
        cash_session: CashSession,
        publish,
    ) -> None:
        self._redis_url = redis_url
        self._chip_client = chip_client
        self._hardware_client = hardware_client
        self._cash_session = cash_session
        self._publish = publish
        self._redis: redis.Redis | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background Redis subscription loop."""
        self._redis = redis.from_url(self._redis_url, decode_responses=True)
        self._task = asyncio.create_task(self._run())
        logger.info("hardware_event_consumer_started")

    async def stop(self) -> None:
        """Cancel the consumer task and close Redis."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    async def _run(self) -> None:
        """Read hardware.events messages until stopped."""
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.subscribe("hardware.events")
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and isinstance(msg.get("data"), str):
                    await self._handle(msg["data"])
                await asyncio.sleep(0.02)
        finally:
            await pubsub.unsubscribe("hardware.events")
            await pubsub.aclose()

    async def _handle(self, raw: str) -> None:
        """Dispatch rfid.scan and cash.inserted events to access handlers."""
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("invalid_hardware_event raw=%s", raw)
            return

        try:
            event_type = event.get("type")
            if event_type == "rfid.scan":
                uid = event.get("uid")
                if not uid:
                    return
                async with SessionLocal() as db:
                    await process_chip_access(
                        str(uid),
                        db,
                        chip_client=self._chip_client,
                        hardware_client=self._hardware_client,
                        publish=self._publish,
                    )
                return

            if event_type == "cash.inserted":
                amount_cents = event.get("amount_cents")
                if amount_cents is None:
                    return
                async with SessionLocal() as db:
                    await process_cash_inserted(
                        int(amount_cents),
                        db,
                        cash_session=self._cash_session,
                        hardware_client=self._hardware_client,
                        publish=self._publish,
                    )
        except Exception:
            logger.exception("hardware_event_handle_failed event=%s", raw)
