from __future__ import annotations

from dataclasses import dataclass

import httpx

from .settings import settings


@dataclass(frozen=True)
class ChipValidation:
    """Chip details returned by the chip-service validate endpoint."""

    chip_id: str
    uid: str
    is_enabled: bool
    balance_cents: int
    holder_name: str | None = None


class ChipClient:
    """HTTP client for chip-service lookups and balance changes."""

    def __init__(self) -> None:
        self._base = settings.chip_service_url.rstrip("/")

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
        body: dict[str, object] = {
            "delta_cents": delta_cents,
            "reason": reason,
            "description": description,
        }
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{self._base}/chips/{chip_id}/balance/adjust", json=body)
        if resp.status_code == 409:
            raise ValueError("insufficient_balance")
        if resp.status_code == 404:
            raise ValueError("balance_not_found")
        resp.raise_for_status()
        return int(resp.json()["amount_cents"])
