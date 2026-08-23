"""Tests for subscription price lookup from access_service.pricing_config."""

from __future__ import annotations

import pytest

from app.pricing import get_subscription_price_cents
from app.settings import settings


class _ScalarResultDb:
    def __init__(self, value: int | None = None, *, raise_exc: Exception | None = None) -> None:
        self.value = value
        self.raise_exc = raise_exc

    async def scalar(self, _statement: object) -> int | None:
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.value


@pytest.mark.asyncio
async def test_subscription_price_from_access_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "subscription_price_cents", 30000)
    db = _ScalarResultDb(35000)
    assert await get_subscription_price_cents(db) == 35000  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_subscription_price_falls_back_when_row_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "subscription_price_cents", 30000)
    db = _ScalarResultDb(None)
    assert await get_subscription_price_cents(db) == 30000  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_subscription_price_falls_back_when_query_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "subscription_price_cents", 27000)
    db = _ScalarResultDb(raise_exc=RuntimeError("relation missing"))
    assert await get_subscription_price_cents(db) == 27000  # type: ignore[arg-type]
