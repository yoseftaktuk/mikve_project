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
    assigned_user_id: str | None
    balance_cents: int


class ChipClient:
    """HTTP client for chip-service registration, validation, and balance changes."""

    def __init__(self) -> None:
        self._base = settings.chip_service_url.rstrip("/")

    async def register(self, uid: str) -> None:
        """Create a chip record for the given UID if it does not already exist."""
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{self._base}/chips", json={"uid": uid})
        if resp.status_code == 400:
            return
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
        )

    async def adjust_balance(self, chip_id: str, delta_cents: int, reason: str, description: str | None = None) -> int:
        """Apply a balance delta and return the new balance in cents."""
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
    """HTTP client for hardware-service door control."""

    def __init__(self) -> None:
        self._base = settings.hardware_service_url.rstrip("/")

    async def open_door(self, seconds: int) -> None:
        """Ask hardware-service to unlock the door for the given seconds."""
        # #region agent log
        import json
        import time
        from pathlib import Path

        _dbg = {
            "sessionId": "359384",
            "runId": "door-pre",
            "hypothesisId": "A",
            "location": "clients.py:open_door",
            "message": "open_door_request",
            "data": {"base": self._base, "seconds": seconds, "url": f"{self._base}/door/open"},
            "timestamp": int(time.time() * 1000),
        }
        try:
            Path("/Users/natankatz/mikve_project/.cursor/debug-359384.log").open("a").write(
                json.dumps(_dbg) + "\n"
            )
        except Exception:
            pass
        # #endregion
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(f"{self._base}/door/open", json={"seconds": seconds})
            # #region agent log
            _dbg2 = {
                "sessionId": "359384",
                "runId": "door-pre",
                "hypothesisId": "A",
                "location": "clients.py:open_door",
                "message": "open_door_response",
                "data": {"base": self._base, "status_code": resp.status_code},
                "timestamp": int(time.time() * 1000),
            }
            try:
                Path("/Users/natankatz/mikve_project/.cursor/debug-359384.log").open("a").write(
                    json.dumps(_dbg2) + "\n"
                )
            except Exception:
                pass
            # #endregion
            resp.raise_for_status()
        except Exception as exc:
            # #region agent log
            _dbg3 = {
                "sessionId": "359384",
                "runId": "door-pre",
                "hypothesisId": "A",
                "location": "clients.py:open_door",
                "message": "open_door_failed",
                "data": {"base": self._base, "error": f"{type(exc).__name__}: {exc}"},
                "timestamp": int(time.time() * 1000),
            }
            try:
                Path("/Users/natankatz/mikve_project/.cursor/debug-359384.log").open("a").write(
                    json.dumps(_dbg3) + "\n"
                )
            except Exception:
                pass
            # #endregion
            raise
