from __future__ import annotations

from dataclasses import dataclass

import httpx

from .settings import settings


@dataclass(frozen=True)
class MemberNationalIdMatch:
    """Ledger member resolved from a Nedarim Zeout / national ID lookup."""

    member_id: str
    uid: str
    is_enabled: bool
    balance_cents: int
    national_id: str | None = None


@dataclass(frozen=True)
class MemberValidation:
    """Ledger details returned by the fingerprints-service validate endpoint."""

    member_id: str
    uid: str
    is_enabled: bool
    balance_cents: int
    holder_name: str | None = None
    subscription_active: bool = False
    subscription_month_name: str | None = None
    subscription_free_entry_available_today: bool = False
    current_hebrew_month_name: str | None = None


class FingerprintsClient:
    """HTTP client for fingerprints-service lookups and balance changes."""

    def __init__(self) -> None:
        self._base = settings.fingerprints_service_url.rstrip("/")

    async def lookup_by_national_id(self, national_id: str) -> MemberNationalIdMatch:
        """Resolve exactly one member by national ID. Raises if none or ambiguous."""
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{self._base}/fingerprints/lookup-by-national-id",
                json={"national_id": national_id},
            )
        if resp.status_code == 404:
            raise ValueError("member_not_found")
        if resp.status_code == 409:
            raise ValueError("national_id_ambiguous")
        resp.raise_for_status()
        data = resp.json()
        return MemberNationalIdMatch(
            member_id=str(data["member_id"]),
            uid=data["uid"],
            is_enabled=bool(data["is_enabled"]),
            balance_cents=int(data["balance_cents"]),
            national_id=data.get("national_id"),
        )

    async def validate(self, uid: str) -> MemberValidation:
        """Fetch ledger status and balance by UID."""
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{self._base}/fingerprints/validate", json={"uid": uid})
        if resp.status_code == 404:
            raise ValueError("member_not_found")
        resp.raise_for_status()
        data = resp.json()
        return MemberValidation(
            member_id=str(data["member_id"]),
            uid=data["uid"],
            is_enabled=bool(data["is_enabled"]),
            balance_cents=int(data["balance_cents"]),
            holder_name=data.get("holder_name"),
            subscription_active=bool(data.get("subscription_active")),
            subscription_month_name=data.get("subscription_month_name"),
            subscription_free_entry_available_today=bool(
                data.get("subscription_free_entry_available_today")
            ),
            current_hebrew_month_name=data.get("current_hebrew_month_name"),
        )

    async def adjust_balance(
        self,
        member_id: str,
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
            resp = await client.post(f"{self._base}/fingerprints/{member_id}/balance/adjust", json=body)
        if resp.status_code == 409:
            raise ValueError("insufficient_balance")
        if resp.status_code == 404:
            raise ValueError("balance_not_found")
        resp.raise_for_status()
        return int(resp.json()["amount_cents"])

    async def activate_subscription(
        self,
        member_id: str,
        *,
        amount_cents: int,
        nedarim_transaction_id: str,
        hebrew_year: int,
        hebrew_month: int,
        hebrew_month_name: str,
    ) -> int:
        """Activate a monthly subscription; return current balance_cents for status display."""
        body = {
            "amount_cents": amount_cents,
            "nedarim_transaction_id": nedarim_transaction_id,
            "hebrew_year": hebrew_year,
            "hebrew_month": hebrew_month,
            "hebrew_month_name": hebrew_month_name,
        }
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{self._base}/fingerprints/{member_id}/subscriptions/activate", json=body
            )
            if resp.status_code == 409:
                detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                code = detail.get("code") if isinstance(detail, dict) else None
                raise ValueError(code or "subscription_already_active")
            if resp.status_code == 404:
                raise ValueError("member_not_found")
            resp.raise_for_status()
            bal = await client.get(f"{self._base}/fingerprints/{member_id}/balance")
            if bal.status_code == 200:
                return int(bal.json()["amount_cents"])
        return 0
