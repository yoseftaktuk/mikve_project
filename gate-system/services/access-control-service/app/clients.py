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
    """Ledger details returned by the fingerprints-service validate endpoint."""

    chip_id: str
    uid: str
    is_enabled: bool
    assigned_user_id: str | None
    balance_cents: int
    holder_name: str | None = None
    national_id: str | None = None
    subscription_active: bool = False
    subscription_month_name: str | None = None
    subscription_free_entry_available_today: bool = False
    current_hebrew_month_name: str | None = None


@dataclass(frozen=True)
class LedgerUser:
    """Registered ledger user with balance for management lists."""

    chip_id: str
    uid: str
    holder_name: str | None
    is_enabled: bool
    balance_cents: int
    national_id: str | None = None
    created_at: str | None = None


class FingerprintsClient:
    """HTTP client for fingerprints-service registration, validation, and balance changes."""

    def __init__(self) -> None:
        self._base = settings.fingerprints_service_url.rstrip("/")

    async def register(
        self,
        uid: str,
        holder_name: str | None = None,
        national_id: str | None = None,
    ) -> None:
        """Create a ledger record for the given UID if it does not already exist."""
        body: dict = {"uid": uid, "holder_name": holder_name}
        if national_id is not None:
            body["national_id"] = national_id
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{self._base}/fingerprints", json=body)
        if resp.status_code == 409:
            raise ValueError("national_id_taken")
        if resp.status_code == 400:
            detail = ""
            try:
                detail = str(resp.json().get("code") or "")
            except Exception:
                detail = ""
            if detail == "national_id_taken":
                raise ValueError("national_id_taken")
            return
        resp.raise_for_status()

    async def rename(
        self,
        chip_id: str,
        holder_name: str | None,
        *,
        national_id: str | None = None,
        set_national_id: bool = False,
    ) -> None:
        """Set the holder name and optional national ID shown on scans and management screens."""
        body: dict = {"holder_name": holder_name}
        if set_national_id:
            body["national_id"] = national_id
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.patch(
                f"{self._base}/fingerprints/{chip_id}/name", json=body
            )
        if resp.status_code == 409:
            raise ValueError("national_id_taken")
        resp.raise_for_status()

    async def list_users(self) -> list[LedgerUser]:
        """List all registered ledger users with balances."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self._base}/fingerprints")
        resp.raise_for_status()
        rows = resp.json()
        return [
            LedgerUser(
                chip_id=str(row["id"]),
                uid=row["uid"],
                holder_name=row.get("holder_name"),
                national_id=row.get("national_id"),
                is_enabled=bool(row["is_enabled"]),
                balance_cents=int(row.get("balance_cents") or 0),
                created_at=row.get("created_at"),
            )
            for row in rows
        ]

    async def update_user(
        self,
        chip_id: str,
        *,
        holder_name: str | None = None,
        national_id: str | None = None,
        is_enabled: bool | None = None,
        set_holder_name: bool = False,
        set_national_id: bool = False,
        set_is_enabled: bool = False,
    ) -> LedgerUser:
        """Update holder name, national ID, and/or enabled flag.

        Use set_* flags so clearing a field (null) is distinct from omit.
        """
        body: dict = {}
        if set_holder_name:
            body["holder_name"] = holder_name
        if set_national_id:
            body["national_id"] = national_id
        if set_is_enabled:
            body["is_enabled"] = is_enabled
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.patch(f"{self._base}/fingerprints/{chip_id}", json=body)
        if resp.status_code == 404:
            raise ValueError("chip_not_found")
        if resp.status_code == 409:
            raise ValueError("national_id_taken")
        if resp.status_code == 400:
            raise ValueError("no_fields")
        resp.raise_for_status()
        row = resp.json()
        return LedgerUser(
            chip_id=str(row["id"]),
            uid=row["uid"],
            holder_name=row.get("holder_name"),
            national_id=row.get("national_id"),
            is_enabled=bool(row["is_enabled"]),
            balance_cents=int(row.get("balance_cents") or 0),
            created_at=row.get("created_at"),
        )

    async def delete_user(self, chip_id: str) -> None:
        """Delete a ledger user and related balance/activity."""
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.delete(f"{self._base}/fingerprints/{chip_id}")
        if resp.status_code == 404:
            raise ValueError("chip_not_found")
        resp.raise_for_status()

    async def get_by_id(self, chip_id: str) -> LedgerUser | None:
        """Fetch a single chip by id with balance; None if missing."""
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{self._base}/fingerprints/{chip_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            row = resp.json()
            bal_resp = await client.get(f"{self._base}/fingerprints/{chip_id}/balance")
            balance_cents = 0
            if bal_resp.status_code == 200:
                balance_cents = int(bal_resp.json().get("amount_cents") or 0)
        return LedgerUser(
            chip_id=str(row["id"]),
            uid=row["uid"],
            holder_name=row.get("holder_name"),
            national_id=row.get("national_id"),
            is_enabled=bool(row["is_enabled"]),
            balance_cents=balance_cents,
            created_at=row.get("created_at"),
        )

    async def validate(self, uid: str) -> ChipValidation:
        """Fetch ledger status and balance by UID."""
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{self._base}/fingerprints/validate", json={"uid": uid})
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
            national_id=data.get("national_id"),
            subscription_active=bool(data.get("subscription_active")),
            subscription_month_name=data.get("subscription_month_name"),
            subscription_free_entry_available_today=bool(
                data.get("subscription_free_entry_available_today")
            ),
            current_hebrew_month_name=data.get("current_hebrew_month_name"),
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
                f"{self._base}/fingerprints/{chip_id}/balance/adjust",
                json=body,
            )
        if resp.status_code == 409:
            raise ValueError("insufficient_balance")
        resp.raise_for_status()
        return int(resp.json()["amount_cents"])

    async def mark_subscription_free_entry(self, chip_id: str) -> None:
        """Record today's free subscription entrance for the chip."""
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{self._base}/fingerprints/{chip_id}/subscriptions/mark-free-entry"
            )
        if resp.status_code == 409:
            raise ValueError("subscription_inactive")
        if resp.status_code == 404:
            raise ValueError("chip_not_found")
        resp.raise_for_status()


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

    async def delete_fingerprint(self, slot: int) -> None:
        """Remove a fingerprint template from the sensor by slot."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{self._base}/fingerprint/delete", json={"slot": slot})
        if resp.status_code == 503:
            raise HardwareUnavailableError(resp.text)
        resp.raise_for_status()
