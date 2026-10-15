from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .clients import FingerprintsClient, FingerprintsClientError, HardwareClient, MemberNotFoundError, NationalIdTakenError
from .access_logic import CashSession
from .models import AccessLog
from .pricing import get_entrance_fee_cents
from .schemas import AccessDecisionResponse

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
    member_id: str
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
    cancelled: bool = False
    processing: bool = False


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
        member_id: str,
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
                member_id=member_id,
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

    def begin_completion(self, session_id: str) -> PendingEnrollment | None:
        """Claim one stored-template event while still allowing cancellation.

        The session remains in the store until the workflow reaches a terminal
        state. That lets the cancel endpoint mark it while an awaited member
        service call is in flight.
        """
        self._purge_expired()
        session = self._sessions.get(session_id)
        if session is None or session.processing:
            return None
        claimed = replace(session, processing=True)
        self._sessions[session_id] = claimed
        return claimed

    def is_cancelled(self, session_id: str) -> bool:
        """Return whether cancellation won while enrollment was processing."""
        self._purge_expired()
        session = self._sessions.get(session_id)
        return session is not None and session.cancelled

    def mark_cancelling(self, session_id: str) -> bool:
        """Mark a pending session as cancelled without removing it.

        Returns True when the session exists (including if already cancelled).
        The session stays so a late fingerprint.enrolled can delete the sensor slot.
        """
        self._purge_expired()
        session = self._sessions.get(session_id)
        if session is None:
            return False
        if not session.cancelled:
            self._sessions[session_id] = replace(session, cancelled=True)
        return True

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
    member_id: str | None,
    reason: str,
    fee_cents: int,
    holder_name: str | None = None,
    balance_cents: int | None = None,
) -> None:
    """Log and announce a denied fingerprint entry."""
    db.add(
        AccessLog(
            member_id=member_id,
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
            "member_id": member_id,
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
    member_client: FingerprintsClient,
    publish: PublishFn,
    approvals: PendingApprovalStore,
    confidence: int | None = None,
    identify: TopupIdentifyStore | None = None,
) -> PendingApproval | None:
    """Resolve a matched slot into a pending approval; never charges by itself.

    When a desk identify session is active, publish fingerprint.identified (or
    fingerprint.identify_failed) instead of creating door approvals / top-up offers.
    """
    fee = await get_entrance_fee_cents(db)
    uid = slot_to_uid(slot)
    identify_mode = identify is not None and identify.is_active()

    try:
        member = await member_client.validate(uid)
    except MemberNotFoundError:
        logger.info("fingerprint_unknown slot=%s identify_mode=%s", slot, identify_mode)
        if identify_mode:
            await _publish_identify_failed(publish, reason="unknown_fingerprint", slot=slot)
            return None
        await _deny(db, publish=publish, uid=uid, member_id=None, reason="unknown_fingerprint", fee_cents=fee)
        return None

    if not member.is_enabled:
        if identify_mode:
            await _publish_identify_failed(publish, reason="member_disabled", slot=slot)
            return None
        await _deny(
            db,
            publish=publish,
            uid=member.uid,
            member_id=member.member_id,
            reason="member_disabled",
            fee_cents=fee,
            holder_name=member.holder_name,
            balance_cents=member.balance_cents,
        )
        return None

    if identify_mode:
        logger.info(
            "fingerprint_identified slot=%s uid=%s balance_cents=%s subscription_active=%s",
            slot,
            member.uid,
            member.balance_cents,
            member.subscription_active,
        )
        await publish(
            {
                "type": "fingerprint.identified",
                "slot": slot,
                "uid": member.uid,
                "member_id": member.member_id,
                "holder_name": member.holder_name,
                "balance_cents": member.balance_cents,
                "subscription_active": member.subscription_active,
                "subscription_month_name": member.subscription_month_name,
                "subscription_free_entry_available_today": member.subscription_free_entry_available_today,
                "current_hebrew_month_name": member.current_hebrew_month_name,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        return None

    use_subscription = (
        member.subscription_active and member.subscription_free_entry_available_today
    )
    charge_fee = 0 if use_subscription else fee

    if not use_subscription and member.balance_cents < fee:
        # Offer top-up choices on the kiosk instead of a dead-end denial toast.
        db.add(
            AccessLog(
                member_id=member.member_id,
                uid=member.uid,
                decision="denied",
                reason="insufficient_balance",
                fee_cents=fee,
                balance_before_cents=member.balance_cents,
                balance_after_cents=member.balance_cents,
            )
        )
        await db.commit()
        logger.info(
            "fingerprint_topup_needed slot=%s uid=%s balance_cents=%s fee_cents=%s",
            slot,
            member.uid,
            member.balance_cents,
            fee,
        )
        await publish(
            {
                "type": "access.topup_needed",
                "method": "fingerprint",
                "uid": member.uid,
                "member_id": member.member_id,
                "holder_name": member.holder_name,
                "balance_cents": member.balance_cents,
                "fee_cents": fee,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        return None

    approval = await approvals.create(
        uid=member.uid,
        member_id=member.member_id,
        holder_name=member.holder_name,
        balance_cents=member.balance_cents,
        fee_cents=charge_fee,
    )
    logger.info(
        "fingerprint_pending slot=%s uid=%s confidence=%s approval_id=%s "
        "subscription_active=%s subscription_free=%s fee_cents=%s",
        slot,
        member.uid,
        confidence,
        approval.approval_id,
        member.subscription_active,
        use_subscription,
        charge_fee,
    )
    await publish(
        {
            "type": "access.pending",
            "method": "fingerprint",
            "approval_id": approval.approval_id,
            "uid": approval.uid,
            "member_id": approval.member_id,
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
        member_id=None,
        reason="unknown_fingerprint",
        fee_cents=await get_entrance_fee_cents(db),
    )


async def approve_pending(
    approval_id: str,
    db: AsyncSession,
    *,
    member_client: FingerprintsClient,
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
        member_client=member_client,
        hardware_client=hardware_client,
        cash_session=session,
        publish=publish,
    )
    return await orch.run_fingerprint_approve(
        approval_id=approval_id,
        uid=approval.uid,
        member_id=approval.member_id,
        holder_name=approval.holder_name,
        balance_cents=approval.balance_cents,
        fee_cents=approval.fee_cents,
        db=db,
    )


async def _fail_enrollment_after_store(
    *,
    session_id: str,
    slot: int,
    reason: str,
    hardware_client: HardwareClient,
    publish: PublishFn,
    created_member_id: str | None = None,
    member_client: FingerprintsClient | None = None,
    renamed_member: "MemberValidation | None" = None,
) -> None:
    """Delete a stored sensor template (and roll back a new member) after enrollment failure."""
    logger.warning(
        "fingerprint_enroll_failed_after_store session_id=%s slot=%s reason=%s",
        session_id,
        slot,
        reason,
    )
    cleanup_failed = False
    try:
        await hardware_client.delete_fingerprint(slot)
    except Exception:
        cleanup_failed = True
        logger.exception(
            "fingerprint_enroll_sensor_cleanup_failed session_id=%s slot=%s reason=%s "
            "manual_sensor_cleanup_required",
            session_id,
            slot,
            reason,
        )

    if created_member_id is not None and member_client is not None:
        try:
            await member_client.delete_user(created_member_id)
        except Exception:
            logger.exception(
                "fingerprint_enroll_member_rollback_failed session_id=%s slot=%s member_id=%s",
                session_id,
                slot,
                created_member_id,
            )

    if renamed_member is not None and member_client is not None:
        try:
            await member_client.rename(
                renamed_member.member_id,
                renamed_member.holder_name,
                national_id=renamed_member.national_id,
                set_national_id=True,
            )
        except Exception:
            logger.exception(
                "fingerprint_enroll_member_restore_failed session_id=%s slot=%s member_id=%s",
                session_id,
                slot,
                renamed_member.member_id,
            )

    event: dict[str, Any] = {
        "type": "fingerprint.enroll_progress",
        "session_id": session_id,
        "step": "failed",
        "reason": reason,
        "slot": slot,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if cleanup_failed:
        event["cleanup_failed"] = True
        event["manual_sensor_cleanup_required"] = True
    await publish(event)


async def complete_enrollment(
    session_id: str,
    slot: int,
    *,
    member_client: FingerprintsClient,
    hardware_client: HardwareClient,
    publish: PublishFn,
    enrollments: PendingEnrollmentStore,
) -> None:
    """Create (or rename) the virtual member for a freshly stored fingerprint template."""
    session = enrollments.begin_completion(session_id)
    if session is None:
        logger.warning("fingerprint_enroll_session_missing session_id=%s slot=%s", session_id, slot)
        return

    if session.cancelled:
        logger.info(
            "fingerprint_enroll_cancelled_cleanup session_id=%s slot=%s",
            session_id,
            slot,
        )
        try:
            await hardware_client.delete_fingerprint(slot)
        except Exception:
            # Member must not be created; sensor may retain an orphan until reconciliation.
            logger.exception(
                "fingerprint_enroll_cancelled_delete_failed session_id=%s slot=%s",
                session_id,
                slot,
            )
        await publish(
            {
                "type": "fingerprint.enroll_progress",
                "session_id": session_id,
                "step": "cancelled",
                "slot": slot,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        enrollments.discard(session_id)
        return

    uid = slot_to_uid(slot)
    created_member_id: str | None = None
    renamed_member = None

    async def cancel_after_store() -> None:
        await _fail_enrollment_after_store(
            session_id=session_id,
            slot=slot,
            reason="cancelled",
            hardware_client=hardware_client,
            publish=publish,
            created_member_id=created_member_id,
            member_client=member_client,
            renamed_member=renamed_member,
        )
        enrollments.discard(session_id)

    try:
        try:
            member = await member_client.validate(uid)
            if enrollments.is_cancelled(session_id):
                await cancel_after_store()
                return
            # Slot reused by the sensor: keep the existing member and their balance, update identity.
            renamed_member = member
            await member_client.rename(
                member.member_id,
                session.holder_name,
                national_id=session.national_id,
                set_national_id=True,
            )
            member_id = member.member_id
            balance_cents = member.balance_cents
        except NationalIdTakenError:
            raise
        except MemberNotFoundError:
            if enrollments.is_cancelled(session_id):
                await cancel_after_store()
                return
            member = await member_client.register(
                uid, holder_name=session.holder_name, national_id=session.national_id
            )
            member_id = member.member_id
            balance_cents = member.balance_cents
            created_member_id = member_id

        if enrollments.is_cancelled(session_id):
            await cancel_after_store()
            return

        if session.initial_amount_cents > 0:
            balance_cents = await member_client.adjust_balance(
                member_id=member_id,
                delta_cents=session.initial_amount_cents,
                reason="management_topup",
                description="initial fingerprint balance",
            )
        if enrollments.is_cancelled(session_id):
            await cancel_after_store()
            return
    except NationalIdTakenError:
        reason = "national_id_taken"
        await _fail_enrollment_after_store(
            session_id=session_id,
            slot=slot,
            reason=reason,
            hardware_client=hardware_client,
            publish=publish,
            created_member_id=created_member_id,
            member_client=member_client,
            renamed_member=renamed_member,
        )
        enrollments.discard(session_id)
        return
    except FingerprintsClientError:
        reason = "enrollment_failed"
        await _fail_enrollment_after_store(
            session_id=session_id,
            slot=slot,
            reason=reason,
            hardware_client=hardware_client,
            publish=publish,
            created_member_id=created_member_id,
            member_client=member_client,
            renamed_member=renamed_member,
        )
        enrollments.discard(session_id)
        return
    except Exception:
        logger.exception(
            "fingerprint_enroll_unexpected session_id=%s slot=%s",
            session_id,
            slot,
        )
        await _fail_enrollment_after_store(
            session_id=session_id,
            slot=slot,
            reason="enrollment_failed",
            hardware_client=hardware_client,
            publish=publish,
            created_member_id=created_member_id,
            member_client=member_client,
            renamed_member=renamed_member,
        )
        enrollments.discard(session_id)
        return

    logger.info(
        "fingerprint_registered uid=%s slot=%s holder_name=%s national_id=%s balance_cents=%s",
        uid,
        slot,
        session.holder_name,
        session.national_id,
        balance_cents,
    )
    enrollments.discard(session_id)
    await publish(
        {
            "type": "fingerprint.registered",
            "session_id": session_id,
            "slot": slot,
            "uid": uid,
            "member_id": member_id,
            "holder_name": session.holder_name,
            "national_id": session.national_id,
            "balance_cents": balance_cents,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
