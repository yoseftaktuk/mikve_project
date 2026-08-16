from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .clients import FingerprintsClient, HardwareClient
from .access_logic import CashSession
from .models import AccessLog
from .schemas import AccessDecisionResponse
from .settings import settings

logger = logging.getLogger(__name__)

PublishFn = Callable[[dict[str, Any]], Awaitable[None]]

UID_PREFIX = "FP-"
ENROLLMENT_TTL_SECONDS = 300


def slot_to_uid(slot: int) -> str:
    """Virtual chip UID for a sensor template slot."""
    return f"{UID_PREFIX}{int(slot):03d}"


def uid_to_slot(uid: str) -> int | None:
    """Sensor slot behind a virtual chip UID, or None for non-fingerprint UIDs."""
    if not uid.startswith(UID_PREFIX):
        return None
    try:
        return int(uid[len(UID_PREFIX) :])
    except ValueError:
        return None


@dataclass(frozen=True)
class PendingApproval:
    """An identified person waiting for staff to confirm the charge."""

    approval_id: str
    uid: str
    chip_id: str
    holder_name: str | None
    balance_cents: int
    fee_cents: int
    expires_at: float

    @property
    def expires_in_seconds(self) -> int:
        return max(0, int(round(self.expires_at - time.monotonic())))


@dataclass(frozen=True)
class PendingEnrollment:
    """Details captured in the UI before the sensor stores a template."""

    session_id: str
    holder_name: str
    national_id: str
    initial_amount_cents: int
    created_at: float


class PendingApprovalStore:
    """Holds the single active fingerprint approval and expires it on a timer."""

    def __init__(self, timeout_seconds: int) -> None:
        self._timeout_seconds = max(1, timeout_seconds)
        self._current: PendingApproval | None = None
        self._lock = asyncio.Lock()
        self._expiry_task: asyncio.Task[None] | None = None
        self._publish: PublishFn | None = None

    @property
    def current(self) -> PendingApproval | None:
        return self._current

    @property
    def timeout_seconds(self) -> int:
        return self._timeout_seconds

    def set_publish(self, publish: PublishFn) -> None:
        """Attach the callback used to emit access.pending_cleared events."""
        self._publish = publish

    async def shutdown(self) -> None:
        """Cancel a pending expiry timer."""
        await self._cancel_timer()

    async def _cancel_timer(self) -> None:
        task = self._expiry_task
        self._expiry_task = None
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def create(
        self,
        *,
        uid: str,
        chip_id: str,
        holder_name: str | None,
        balance_cents: int,
        fee_cents: int,
    ) -> PendingApproval:
        """Replace any current approval with a fresh one and start its timer."""
        await self._cancel_timer()
        async with self._lock:
            replaced = self._current
            approval = PendingApproval(
                approval_id=secrets.token_urlsafe(12),
                uid=uid,
                chip_id=chip_id,
                holder_name=holder_name,
                balance_cents=balance_cents,
                fee_cents=fee_cents,
                expires_at=time.monotonic() + self._timeout_seconds,
            )
            self._current = approval

        if replaced is not None:
            await self._emit_cleared(replaced, "replaced")
        self._expiry_task = asyncio.create_task(self._expire_after_timeout(approval.approval_id))
        return approval

    async def _expire_after_timeout(self, approval_id: str) -> None:
        try:
            await asyncio.sleep(self._timeout_seconds)
        except asyncio.CancelledError:
            return
        self._expiry_task = None
        await self.clear(approval_id, reason="timeout")

    async def consume(self, approval_id: str) -> PendingApproval:
        """Atomically take the approval so it can only be charged once."""
        async with self._lock:
            approval = self._current
            if approval is None or approval.approval_id != approval_id:
                raise ValueError("approval_not_found")
            if approval.expires_at <= time.monotonic():
                self._current = None
                raise ValueError("approval_expired")
            self._current = None
        await self._cancel_timer()
        return approval

    async def clear(self, approval_id: str, *, reason: str) -> bool:
        """Drop the approval (timeout or cancel) and notify dashboards."""
        async with self._lock:
            approval = self._current
            if approval is None or approval.approval_id != approval_id:
                return False
            self._current = None
        await self._cancel_timer()
        await self._emit_cleared(approval, reason)
        return True

    async def _emit_cleared(self, approval: PendingApproval, reason: str) -> None:
        logger.info("fingerprint_approval_cleared uid=%s reason=%s", approval.uid, reason)
        if self._publish is None:
            return
        await self._publish(
            {
                "type": "access.pending_cleared",
                "approval_id": approval.approval_id,
                "uid": approval.uid,
                "holder_name": approval.holder_name,
                "reason": reason,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )


class PendingEnrollmentStore:
    """Keeps enrollment details until the sensor reports a stored template."""

    def __init__(self, ttl_seconds: int = ENROLLMENT_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, PendingEnrollment] = {}

    def create(
        self, *, holder_name: str, national_id: str, initial_amount_cents: int = 0
    ) -> PendingEnrollment:
        """Register a new enrollment session and return it."""
        self._purge_expired()
        session = PendingEnrollment(
            session_id=secrets.token_urlsafe(9),
            holder_name=holder_name,
            national_id=national_id,
            initial_amount_cents=max(0, initial_amount_cents),
            created_at=time.monotonic(),
        )
        self._sessions[session.session_id] = session
        return session

    def pop(self, session_id: str) -> PendingEnrollment | None:
        """Take a session by id, dropping it from the store."""
        self._purge_expired()
        return self._sessions.pop(session_id, None)

    def discard(self, session_id: str) -> None:
        """Forget a session without consuming its result."""
        self._sessions.pop(session_id, None)

    def clear(self) -> None:
        """Forget all sessions."""
        self._sessions.clear()

    def _purge_expired(self) -> None:
        cutoff = time.monotonic() - self._ttl_seconds
        for session_id in [sid for sid, s in self._sessions.items() if s.created_at < cutoff]:
            del self._sessions[session_id]


class TopupIdentifyStore:
    """Marks the desk money-topup page as listening for fingerprint identity only."""

    def __init__(self, ttl_seconds: int = ENROLLMENT_TTL_SECONDS) -> None:
        self._ttl_seconds = max(1, ttl_seconds)
        self._active_until: float | None = None
        self._lock = asyncio.Lock()

    def is_active(self) -> bool:
        """True while a desk identify session has not expired."""
        if self._active_until is None:
            return False
        if time.monotonic() >= self._active_until:
            self._active_until = None
            return False
        return True

    async def start(self) -> None:
        """Activate (or refresh) identify mode for the TTL window."""
        async with self._lock:
            self._active_until = time.monotonic() + self._ttl_seconds

    async def cancel(self) -> None:
        """Deactivate identify mode immediately."""
        async with self._lock:
            self._active_until = None

    def clear(self) -> None:
        """Drop the session without locking (shutdown path)."""
        self._active_until = None



async def _deny(
    db: AsyncSession,
    *,
    publish: PublishFn,
    uid: str | None,
    chip_id: str | None,
    reason: str,
    fee_cents: int,
    holder_name: str | None = None,
    balance_cents: int | None = None,
) -> None:
    """Log and announce a denied fingerprint entry."""
    db.add(
        AccessLog(
            chip_id=chip_id,
            uid=uid,
            decision="denied",
            reason=reason,
            fee_cents=fee_cents,
            balance_before_cents=balance_cents,
            balance_after_cents=balance_cents,
        )
    )
    await db.commit()
    await publish(
        {
            "type": "access.denied",
            "method": "fingerprint",
            "uid": uid,
            "chip_id": chip_id,
            "holder_name": holder_name,
            "reason": reason,
            "fee_cents": fee_cents,
            "balance_cents": balance_cents,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )


async def _publish_identify_failed(publish: PublishFn, *, reason: str, slot: int | None = None) -> None:
    """Announce a desk-identify failure without creating an entrance denial."""
    await publish(
        {
            "type": "fingerprint.identify_failed",
            "reason": reason,
            "slot": slot,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )


async def process_fingerprint_scan(
    slot: int,
    db: AsyncSession,
    *,
    chip_client: FingerprintsClient,
    publish: PublishFn,
    approvals: PendingApprovalStore,
    confidence: int | None = None,
    identify: TopupIdentifyStore | None = None,
) -> PendingApproval | None:
    """Resolve a matched slot into a pending approval; never charges by itself.

    When a desk identify session is active, publish fingerprint.identified (or
    fingerprint.identify_failed) instead of creating door approvals / top-up offers.
    """
    fee = settings.entrance_fee_cents
    uid = slot_to_uid(slot)
    identify_mode = identify is not None and identify.is_active()

    try:
        chip = await chip_client.validate(uid)
    except ValueError:
        logger.info("fingerprint_unknown slot=%s identify_mode=%s", slot, identify_mode)
        if identify_mode:
            await _publish_identify_failed(publish, reason="unknown_fingerprint", slot=slot)
            return None
        await _deny(db, publish=publish, uid=uid, chip_id=None, reason="unknown_fingerprint", fee_cents=fee)
        return None

    if not chip.is_enabled:
        if identify_mode:
            await _publish_identify_failed(publish, reason="chip_disabled", slot=slot)
            return None
        await _deny(
            db,
            publish=publish,
            uid=chip.uid,
            chip_id=chip.chip_id,
            reason="chip_disabled",
            fee_cents=fee,
            holder_name=chip.holder_name,
            balance_cents=chip.balance_cents,
        )
        return None

    if identify_mode:
        logger.info(
            "fingerprint_identified slot=%s uid=%s balance_cents=%s subscription_active=%s",
            slot,
            chip.uid,
            chip.balance_cents,
            chip.subscription_active,
        )
        await publish(
            {
                "type": "fingerprint.identified",
                "slot": slot,
                "uid": chip.uid,
                "chip_id": chip.chip_id,
                "holder_name": chip.holder_name,
                "balance_cents": chip.balance_cents,
                "subscription_active": chip.subscription_active,
                "subscription_month_name": chip.subscription_month_name,
                "subscription_free_entry_available_today": chip.subscription_free_entry_available_today,
                "current_hebrew_month_name": chip.current_hebrew_month_name,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        return None

    use_subscription = (
        chip.subscription_active and chip.subscription_free_entry_available_today
    )
    charge_fee = 0 if use_subscription else fee

    if not use_subscription and chip.balance_cents < fee:
        # Offer top-up choices on the kiosk instead of a dead-end denial toast.
        db.add(
            AccessLog(
                chip_id=chip.chip_id,
                uid=chip.uid,
                decision="denied",
                reason="insufficient_balance",
                fee_cents=fee,
                balance_before_cents=chip.balance_cents,
                balance_after_cents=chip.balance_cents,
            )
        )
        await db.commit()
        logger.info(
            "fingerprint_topup_needed slot=%s uid=%s balance_cents=%s fee_cents=%s",
            slot,
            chip.uid,
            chip.balance_cents,
            fee,
        )
        await publish(
            {
                "type": "access.topup_needed",
                "method": "fingerprint",
                "uid": chip.uid,
                "chip_id": chip.chip_id,
                "holder_name": chip.holder_name,
                "balance_cents": chip.balance_cents,
                "fee_cents": fee,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        return None

    approval = await approvals.create(
        uid=chip.uid,
        chip_id=chip.chip_id,
        holder_name=chip.holder_name,
        balance_cents=chip.balance_cents,
        fee_cents=charge_fee,
    )
    logger.info(
        "fingerprint_pending slot=%s uid=%s confidence=%s approval_id=%s "
        "subscription_active=%s subscription_free=%s fee_cents=%s",
        slot,
        chip.uid,
        confidence,
        approval.approval_id,
        chip.subscription_active,
        use_subscription,
        charge_fee,
    )
    await publish(
        {
            "type": "access.pending",
            "method": "fingerprint",
            "approval_id": approval.approval_id,
            "uid": approval.uid,
            "chip_id": approval.chip_id,
            "holder_name": approval.holder_name,
            "balance_cents": approval.balance_cents,
            "fee_cents": approval.fee_cents,
            "payment_method": "subscription" if charge_fee == 0 else "balance",
            "expires_in_seconds": approval.expires_in_seconds,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    return approval


async def process_fingerprint_unmatched(
    db: AsyncSession,
    *,
    publish: PublishFn,
    identify: TopupIdentifyStore | None = None,
) -> None:
    """Announce a finger that matches no stored template."""
    if identify is not None and identify.is_active():
        await _publish_identify_failed(publish, reason="unmatched")
        return
    await _deny(
        db,
        publish=publish,
        uid=None,
        chip_id=None,
        reason="unknown_fingerprint",
        fee_cents=settings.entrance_fee_cents,
    )


async def approve_pending(
    approval_id: str,
    db: AsyncSession,
    *,
    chip_client: FingerprintsClient,
    hardware_client: HardwareClient,
    publish: PublishFn,
    approvals: PendingApprovalStore,
    cash_session: CashSession | None = None,
) -> AccessDecisionResponse:
    """Charge the entrance fee for a confirmed approval via the access saga."""
    from .saga import AccessOrchestrator

    approval = await approvals.consume(approval_id)
    session = cash_session if cash_session is not None else CashSession(timeout_seconds=1)
    orch = AccessOrchestrator(
        chip_client=chip_client,
        hardware_client=hardware_client,
        cash_session=session,
        publish=publish,
    )
    return await orch.run_fingerprint_approve(
        approval_id=approval_id,
        uid=approval.uid,
        chip_id=approval.chip_id,
        holder_name=approval.holder_name,
        balance_cents=approval.balance_cents,
        fee_cents=approval.fee_cents,
        db=db,
    )


async def complete_enrollment(
    session_id: str,
    slot: int,
    *,
    chip_client: FingerprintsClient,
    publish: PublishFn,
    enrollments: PendingEnrollmentStore,
) -> None:
    """Create (or rename) the virtual chip for a freshly stored template."""
    session = enrollments.pop(session_id)
    if session is None:
        logger.warning("fingerprint_enroll_session_missing session_id=%s slot=%s", session_id, slot)
        return

    uid = slot_to_uid(slot)
    try:
        try:
            chip = await chip_client.validate(uid)
            # Slot reused by the sensor: keep the existing chip and its balance, update identity.
            await chip_client.rename(
                chip.chip_id,
                session.holder_name,
                national_id=session.national_id,
                set_national_id=True,
            )
            chip_id = chip.chip_id
            balance_cents = chip.balance_cents
        except ValueError as exc:
            if str(exc) == "national_id_taken":
                raise
            await chip_client.register(
                uid, holder_name=session.holder_name, national_id=session.national_id
            )
            chip = await chip_client.validate(uid)
            chip_id = chip.chip_id
            balance_cents = chip.balance_cents
    except ValueError as exc:
        if str(exc) == "national_id_taken":
            logger.warning(
                "fingerprint_enroll_national_id_taken session_id=%s national_id=%s",
                session_id,
                session.national_id,
            )
            await publish(
                {
                    "type": "fingerprint.enroll_progress",
                    "session_id": session_id,
                    "step": "failed",
                    "reason": "national_id_taken",
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
            return
        raise

    if session.initial_amount_cents > 0:
        balance_cents = await chip_client.adjust_balance(
            chip_id=chip_id,
            delta_cents=session.initial_amount_cents,
            reason="management_topup",
            description="initial fingerprint balance",
        )

    logger.info(
        "fingerprint_registered uid=%s slot=%s holder_name=%s national_id=%s balance_cents=%s",
        uid,
        slot,
        session.holder_name,
        session.national_id,
        balance_cents,
    )
    await publish(
        {
            "type": "fingerprint.registered",
            "session_id": session_id,
            "slot": slot,
            "uid": uid,
            "chip_id": chip_id,
            "holder_name": session.holder_name,
            "national_id": session.national_id,
            "balance_cents": balance_cents,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
