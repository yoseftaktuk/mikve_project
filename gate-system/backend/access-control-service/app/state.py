"""Shared runtime objects for access-control startup and routes."""

from __future__ import annotations

import json

import redis.asyncio as redis

from .access_logic import CashSession
from .clients import FingerprintsClient, HardwareClient
from .fingerprint_logic import PendingApprovalStore, PendingEnrollmentStore, TopupIdentifyStore
from .hardware_consumer import HardwareEventConsumer
from .realtime import PubSubFanout
from .saga import AccessAttemptReconciler, AccessOrchestrator
from .settings import settings

member_client = FingerprintsClient()
hardware_client = HardwareClient()
cash_session = CashSession(timeout_seconds=settings.cash_session_timeout_seconds)
approvals = PendingApprovalStore(timeout_seconds=settings.fingerprint_approval_timeout_seconds)
enrollments = PendingEnrollmentStore()
identify_sessions = TopupIdentifyStore()
fanout = PubSubFanout(settings.redis_url)
hardware_consumer: HardwareEventConsumer | None = None
redis_client: redis.Redis | None = None
orchestrator: AccessOrchestrator | None = None
reconciler: AccessAttemptReconciler | None = None


async def publish(event: dict) -> None:
    """Publish an access event to Redis and connected dashboards."""
    if redis_client is None:
        return
    await redis_client.publish("access.events", json.dumps(event))
    await fanout.publish_local(event)
