from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from .nedarim_plus import CreateTransactionCommand, CreateTransactionResult, NedarimPlusClient
from .settings import settings


@runtime_checkable
class PaymentProvider(Protocol):
    """Abstraction for Nedarim Plus or local mock card clearing."""

    @property
    def is_configured(self) -> bool:
        """Whether the provider can open a card top-up transaction."""
        ...

    @property
    def iframe_url(self) -> str:
        """URL the kiosk iframe loads (empty in mock mode)."""
        ...

    async def create_transaction(self, command: CreateTransactionCommand) -> CreateTransactionResult:
        """Open a pending card transaction and return its provider id."""
        ...


class NedarimPaymentProvider:
    """Real Nedarim Plus clearing via matara.pro."""

    def __init__(self) -> None:
        self._client = NedarimPlusClient()

    @property
    def is_configured(self) -> bool:
        return self._client.is_configured

    @property
    def iframe_url(self) -> str:
        return settings.nedarim_iframe_url

    async def create_transaction(self, command: CreateTransactionCommand) -> CreateTransactionResult:
        return await self._client.create_transaction(command)


class MockPaymentProvider:
    """Fake card clearing for local development without Nedarim or Cloudflare."""

    @property
    def is_configured(self) -> bool:
        return True

    @property
    def iframe_url(self) -> str:
        return ""

    async def create_transaction(self, command: CreateTransactionCommand) -> CreateTransactionResult:
        return CreateTransactionResult(transaction_id=f"MOCK-{uuid.uuid4().hex[:12]}")


def build_payment_provider() -> PaymentProvider:
    """Select mock or real provider from PAYMENT_MODE."""
    if settings.payment_mode == "mock":
        return MockPaymentProvider()
    return NedarimPaymentProvider()
