from __future__ import annotations

from dataclasses import dataclass

import httpx

from .settings import settings


@dataclass(frozen=True)
class ChipValidation:
    chip_id: str
    uid: str
    is_enabled: bool
    assigned_user_id: str | None
    balance_cents: int


class ChipClient:
    def __init__(self) -> None:
        self._base = settings.chip_service_url.rstrip("/")

    async def register(self, uid: str) -> None:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{self._base}/chips", json={"uid": uid})
        if resp.status_code == 400:
            return
        resp.raise_for_status()

    async def validate(self, uid: str) -> ChipValidation:
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
        )

    async def adjust_balance(self, chip_id: str, delta_cents: int, reason: str, description: str | None = None) -> int:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{self._base}/chips/{chip_id}/balance/adjust",
                json={"delta_cents": delta_cents, "reason": reason, "description": description},
            )
        if resp.status_code == 409:
            raise ValueError("insufficient_balance")
        resp.raise_for_status()
        return int(resp.json()["amount_cents"])


class HardwareClient:
    def __init__(self) -> None:
        self._base = settings.hardware_service_url.rstrip("/")

    async def open_door(self, seconds: int) -> None:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{self._base}/door/open", json={"seconds": seconds})
        resp.raise_for_status()

