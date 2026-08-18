from __future__ import annotations

import asyncio
import json
import logging

import redis.asyncio as redis

from .access_logic import CashSession, process_cash_inserted
from .clients import FingerprintsClient, HardwareClient
from .db import SessionLocal
from .fingerprint_logic import (
    PendingApprovalStore,
    PendingEnrollmentStore,
    TopupIdentifyStore,
    complete_enrollment,
    process_fingerprint_scan,
    process_fingerprint_unmatched,
)

logger = logging.getLogger(__name__)


class HardwareEventConsumer:
    """Subscribes to hardware.events and runs cash/fingerprint access logic."""

    def __init__(
        self,
        redis_url: str,
        *,
        member_client: FingerprintsClient,
        hardware_client: HardwareClient,
        cash_session: CashSession,
        publish,
        approvals: PendingApprovalStore,
        enrollments: PendingEnrollmentStore,
        identify: TopupIdentifyStore,
    ) -> None:
        self._redis_url = redis_url
        self._member_client = member_client
        self._hardware_client = hardware_client
        self._cash_session = cash_session
        self._publish = publish
        self._approvals = approvals
        self._enrollments = enrollments
        self._identify = identify
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
        """Dispatch cash and fingerprint events to access handlers."""
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("invalid_hardware_event raw=%s", raw)
            return

        try:
            event_type = event.get("type")

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
                        member_client=self._member_client,
                    )
                return

            if event_type == "fingerprint.scan":
                slot = event.get("slot")
                if slot is None:
                    return
                async with SessionLocal() as db:
                    await process_fingerprint_scan(
                        int(slot),
                        db,
                        member_client=self._member_client,
                        publish=self._publish,
                        approvals=self._approvals,
                        confidence=event.get("confidence"),
                        identify=self._identify,
                    )
                return

            if event_type == "fingerprint.unmatched":
                async with SessionLocal() as db:
                    await process_fingerprint_unmatched(
                        db, publish=self._publish, identify=self._identify
                    )
                return

            if event_type == "fingerprint.enrolled":
                session_id = event.get("session_id")
                slot = event.get("slot")
                if not session_id or slot is None:
                    return
                await complete_enrollment(
                    str(session_id),
                    int(slot),
                    member_client=self._member_client,
                    publish=self._publish,
                    enrollments=self._enrollments,
                )
                return
        except Exception:
            logger.exception("hardware_event_handle_failed event=%s", raw)
