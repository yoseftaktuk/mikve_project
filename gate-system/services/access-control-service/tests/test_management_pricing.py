"""Tests for management pricing catalog GET/PATCH and helpers."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Response
from fastapi.testclient import TestClient

from app import management as mgmt
from app.management import (
    MANAGEMENT_COOKIE_NAME,
    ManagementAuthResponse,
    ManagementPinRequest,
    ManagementPricingResponse,
    ManagementPricingUpdateRequest,
    authenticate_pin,
    require_management_token,
)
from app.models import PricingConfig
from app.pricing import ensure_pricing_row, get_pricing, update_pricing
from app.settings import settings


class FakeDb:
    """Minimal AsyncSession stand-in for pricing catalog tests."""

    def __init__(self) -> None:
        self.rows: list[object] = []
        self.commits = 0
        self._by_id: dict[object, object] = {}

    def add(self, row: object) -> None:
        self.rows.append(row)
        row_id = getattr(row, "id", None)
        if row_id is not None:
            self._by_id[row_id] = row

    async def flush(self) -> None:
        for row in self.rows:
            row_id = getattr(row, "id", None)
            if row_id is not None:
                self._by_id[row_id] = row

    async def commit(self) -> None:
        self.commits += 1
        await self.flush()

    async def get(self, model: type, key: object) -> object | None:
        assert model is PricingConfig
        return self._by_id.get(key)


@pytest.fixture(autouse=True)
def _reset_tokens_and_pin(monkeypatch: pytest.MonkeyPatch):
    mgmt._mgmt_tokens.clear()
    monkeypatch.setattr(settings, "management_pin", "test-pin-1234")
    monkeypatch.setattr(settings, "environment", "dev")
    monkeypatch.setattr(settings, "management_cookie_secure", None)
    monkeypatch.setattr(settings, "entrance_fee_cents", 1500)
    monkeypatch.setattr(settings, "subscription_price_cents", 30000)
    yield
    mgmt._mgmt_tokens.clear()


@pytest.mark.asyncio
async def test_ensure_pricing_row_seeds_from_env() -> None:
    db = FakeDb()
    row = await ensure_pricing_row(db)  # type: ignore[arg-type]
    assert row.entrance_fee_cents == 1500
    assert row.subscription_price_cents == 30000
    assert db.commits == 0

    again = await ensure_pricing_row(db)  # type: ignore[arg-type]
    assert again is row
    assert db.commits == 0


@pytest.mark.asyncio
async def test_update_pricing_persists_both_fields() -> None:
    db = FakeDb()
    pricing = await update_pricing(
        db,  # type: ignore[arg-type]
        entrance_fee_cents=2000,
        subscription_price_cents=35000,
    )
    assert pricing.entrance_fee_cents == 2000
    assert pricing.subscription_price_cents == 35000
    loaded = await get_pricing(db)  # type: ignore[arg-type]
    assert loaded == pricing


@pytest.mark.asyncio
async def test_update_pricing_rejects_invalid_amount() -> None:
    db = FakeDb()
    with pytest.raises(ValueError, match="invalid_entrance_fee_cents"):
        await update_pricing(db, entrance_fee_cents=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="invalid_subscription_price_cents"):
        await update_pricing(db, subscription_price_cents=1_000_001)  # type: ignore[arg-type]


@pytest.fixture
def pricing_client() -> TestClient:
    db = FakeDb()
    app = FastAPI()

    @app.post("/management/auth", response_model=ManagementAuthResponse)
    async def auth(req: ManagementPinRequest, response: Response):
        return await authenticate_pin(req, response)

    @app.get(
        "/management/pricing",
        response_model=ManagementPricingResponse,
        dependencies=[Depends(require_management_token)],
    )
    async def pricing_get():
        pricing = await get_pricing(db)  # type: ignore[arg-type]
        return ManagementPricingResponse(
            entrance_fee_cents=pricing.entrance_fee_cents,
            subscription_price_cents=pricing.subscription_price_cents,
        )

    @app.patch(
        "/management/pricing",
        response_model=ManagementPricingResponse,
        dependencies=[Depends(require_management_token)],
    )
    async def pricing_patch(req: ManagementPricingUpdateRequest):
        from fastapi import HTTPException, status

        try:
            pricing = await update_pricing(
                db,  # type: ignore[arg-type]
                entrance_fee_cents=(
                    req.entrance_fee_cents if "entrance_fee_cents" in req.model_fields_set else None
                ),
                subscription_price_cents=(
                    req.subscription_price_cents
                    if "subscription_price_cents" in req.model_fields_set
                    else None
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
        return ManagementPricingResponse(
            entrance_fee_cents=pricing.entrance_fee_cents,
            subscription_price_cents=pricing.subscription_price_cents,
        )

    return TestClient(app)


def test_pricing_requires_management_auth(pricing_client: TestClient) -> None:
    denied = pricing_client.get("/management/pricing")
    assert denied.status_code == 401


def test_pricing_get_and_patch(pricing_client: TestClient) -> None:
    login = pricing_client.post("/management/auth", json={"pin": "test-pin-1234"})
    assert login.status_code == 200
    assert MANAGEMENT_COOKIE_NAME in pricing_client.cookies

    got = pricing_client.get("/management/pricing")
    assert got.status_code == 200
    assert got.json() == {"entrance_fee_cents": 1500, "subscription_price_cents": 30000}

    patched = pricing_client.patch(
        "/management/pricing",
        json={"entrance_fee_cents": 1800, "subscription_price_cents": 32000},
    )
    assert patched.status_code == 200
    assert patched.json() == {"entrance_fee_cents": 1800, "subscription_price_cents": 32000}

    again = pricing_client.get("/management/pricing")
    assert again.json() == {"entrance_fee_cents": 1800, "subscription_price_cents": 32000}


def test_pricing_patch_rejects_empty_body(pricing_client: TestClient) -> None:
    pricing_client.post("/management/auth", json={"pin": "test-pin-1234"})
    response = pricing_client.patch("/management/pricing", json={})
    assert response.status_code == 422
