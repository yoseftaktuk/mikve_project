from __future__ import annotations

from dataclasses import dataclass

import httpx

from .settings import settings


class HardwareUnavailableError(Exception):
    """Door hardware is unreachable or reports unavailable."""


class DoorRejectedError(Exception):
    """Door open was rejected (busy / policy)."""


@dataclass(frozen=True)
class ChipValidation:
    """Chip details returned by the chip-service validate endpoint."""

    chip_id: str
    uid: str
    is_enabled: bool
    assigned_user_id: str | None
    balance_cents: int
    holder_name: str | None = None


class ChipClient:
    """HTTP client for chip-service registration, validation, and balance changes."""

    def __init__(self) -> None:
        self._base = settings.chip_service_url.rstrip("/")

    async def register(self, uid: str, holder_name: str | None = None) -> None:
        """Create a chip record for the given UID if it does not already exist."""
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{self._base}/chips", json={"uid": uid, "holder_name": holder_name})
        if resp.status_code == 400:
            return
        resp.raise_for_status()

    async def rename(self, chip_id: str, holder_name: str | None) -> None:
        """Set the holder name shown on scans and management screens."""
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.patch(
                f"{self._base}/chips/{chip_id}/name", json={"holder_name": holder_name}
            )
        resp.raise_for_status()

    async def validate(self, uid: str) -> ChipValidation:
        """Fetch chip status and balance by UID."""
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{self._base}/chips/validate", json={"uid": uid})
        if resp.status_code == 404:
            raise ValueError("chip_not_found")
        resp.raise_for_status()
        data = resp.json()
        return ChipValidation(
            chip_id=str(data["chip_id"]),
            uid=data["uid"],
            is_enabled=bool(data["is_enabled"]),
            assigned_user_id=data.get("assigned_user_id"),
            balance_cents=int(data["balance_cents"]),
            holder_name=data.get("holder_name"),
        )

    async def adjust_balance(
        self,
        chip_id: str,
        delta_cents: int,
        reason: str,
        description: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        """Apply a balance delta and return the new balance in cents."""
        body: dict = {"delta_cents": delta_cents, "reason": reason, "description": description}
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{self._base}/chips/{chip_id}/balance/adjust",
                json=body,
            )
        if resp.status_code == 409:
            raise ValueError("insufficient_balance")
        resp.raise_for_status()
        return int(resp.json()["amount_cents"])


class HardwareClient:
    """HTTP client for hardware-service door and fingerprint control."""

    def __init__(self) -> None:
        self._base = settings.hardware_service_url.rstrip("/")

    async def open_door(
        self,
        seconds: int,
        *,
        operation_id: str | None = None,
        attempt_id: str | None = None,
        correlation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        """Ask hardware-service to unlock the door; prefer confirmed response."""
        body: dict = {"seconds": seconds}
        if operation_id:
            body["operation_id"] = operation_id
        if attempt_id:
            body["attempt_id"] = attempt_id
        if correlation_id:
            body["correlation_id"] = correlation_id
        timeout = timeout_seconds if timeout_seconds is not None else 5.0
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self._base}/door/open", json=body)
        if resp.status_code == 204:
            return {"status": "confirmed", "unlocked_for_seconds": seconds, "operation_id": operation_id}
        if resp.status_code == 503:
            raise HardwareUnavailableError(resp.text)
        if resp.status_code == 409:
            raise DoorRejectedError(resp.text)
        resp.raise_for_status()
        if resp.content:
            return resp.json()
        return {"status": "confirmed", "unlocked_for_seconds": seconds, "operation_id": operation_id}

    async def enroll_fingerprint(self, session_id: str) -> None:
        """Start a fingerprint enrollment; progress arrives via hardware.events."""
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{self._base}/fingerprint/enroll", json={"session_id": session_id})
        resp.raise_for_status()

    async def cancel_enroll(self) -> None:
        """Abort a running fingerprint enrollment."""
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{self._base}/fingerprint/enroll/cancel")
        resp.raise_for_status()
