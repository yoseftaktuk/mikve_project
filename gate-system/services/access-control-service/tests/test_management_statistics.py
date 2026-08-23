"""Tests for management statistics range helpers, aggregation shaping, and auth."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import Depends, FastAPI, Response
from fastapi.testclient import TestClient

from app import management as mgmt
from app.management import (
    MANAGEMENT_COOKIE_NAME,
    ManagementAuthResponse,
    ManagementPinRequest,
    authenticate_pin,
    require_management_token,
)
from app.settings import settings
from app.statistics import (
    ManagementStatisticsResponse,
    VALID_RANGES,
    approval_rate,
    fetch_management_statistics,
    resolve_statistics_range,
)

JERUSALEM = ZoneInfo("Asia/Jerusalem")


@pytest.fixture(autouse=True)
def _reset_tokens_and_pin(monkeypatch: pytest.MonkeyPatch):
    mgmt._mgmt_tokens.clear()
    monkeypatch.setattr(settings, "management_pin", "test-pin-1234")
    monkeypatch.setattr(settings, "environment", "dev")
    monkeypatch.setattr(settings, "management_cookie_secure", None)
    yield
    mgmt._mgmt_tokens.clear()


def test_approval_rate_empty_and_mixed() -> None:
    assert approval_rate(0, 0) == 0.0
    assert approval_rate(3, 1) == 0.75
    assert approval_rate(0, 5) == 0.0


def test_resolve_range_today_boundaries() -> None:
    now = datetime(2026, 8, 21, 15, 30, tzinfo=JERUSALEM)
    window = resolve_statistics_range("today", now=now)
    assert window.range == "today"
    assert window.timezone == "Asia/Jerusalem"
    assert window.start == datetime(2026, 8, 21, 0, 0, tzinfo=JERUSALEM)
    assert window.end == datetime(2026, 8, 22, 0, 0, tzinfo=JERUSALEM)


def test_resolve_range_7d_inclusive_calendar_days() -> None:
    now = datetime(2026, 8, 21, 10, 0, tzinfo=JERUSALEM)
    window = resolve_statistics_range("7d", now=now)
    assert window.start == datetime(2026, 8, 15, 0, 0, tzinfo=JERUSALEM)
    assert window.end == datetime(2026, 8, 22, 0, 0, tzinfo=JERUSALEM)


def test_resolve_range_month_from_first_day() -> None:
    now = datetime(2026, 8, 21, 10, 0, tzinfo=JERUSALEM)
    window = resolve_statistics_range("month", now=now)
    assert window.start == datetime(2026, 8, 1, 0, 0, tzinfo=JERUSALEM)
    assert window.end == datetime(2026, 8, 22, 0, 0, tzinfo=JERUSALEM)


def test_resolve_range_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="invalid_range"):
        resolve_statistics_range("custom")


class _FakeResult:
    def __init__(self, rows: list[object]):
        self._rows = rows

    def one(self) -> object:
        return self._rows[0]

    def all(self) -> list[object]:
        return list(self._rows)


class _FakeSession:
    """Dispatches canned rows by SQL substring for fetch_management_statistics."""

    def __init__(self, responses: dict[str, list[object]]):
        self._responses = responses
        self.calls: list[str] = []

    async def execute(self, statement, params=None):  # noqa: ANN001
        sql = str(statement)
        self.calls.append(sql)
        # Most-specific markers first so overlapping substrings do not collide.
        priority = [
            "GROUP BY day",
            "GROUP BY reason",
            "payment_transactions",
            "fingerprints_service.members",
            "fingerprints_service.balances",
            "FAILED', 'MANUAL_REVIEW",
            "door_operations",
            "status IN ('pending', 'crediting')",
            "product = 'monthly_subscription'",
            "SUM(fee_cents) FILTER",
        ]
        for key in priority:
            if key in sql and key in self._responses:
                return _FakeResult(self._responses[key])
        for key, rows in self._responses.items():
            if key in sql:
                return _FakeResult(rows)
        raise AssertionError(f"No fake response for SQL: {sql[:200]}")


def _empty_stats_session() -> _FakeSession:
    return _FakeSession(
        {
            "SUM(fee_cents) FILTER": [
                SimpleNamespace(granted_count=0, denied_count=0, entrance_fee_total_cents=0)
            ],
            "GROUP BY day": [],
            "GROUP BY reason": [],
            "payment_transactions": [],
            "product = 'monthly_subscription'": [
                SimpleNamespace(
                    paid_card_topup_count=0,
                    paid_card_topup_total_cents=0,
                    paid_monthly_subscription_count=0,
                    paid_monthly_subscription_total_cents=0,
                )
            ],
            "status IN ('pending', 'crediting')": [
                SimpleNamespace(pending_card_topups=0, crediting_card_topups=0)
            ],
            "fingerprints_service.members": [
                SimpleNamespace(enabled_member_count=0, disabled_member_count=0)
            ],
            "fingerprints_service.balances": [
                SimpleNamespace(total_member_balances_cents=0)
            ],
            "FAILED', 'MANUAL_REVIEW": [SimpleNamespace(failed_access_attempts=0)],
            "door_operations": [SimpleNamespace(door_operation_failures=0)],
        }
    )


@pytest.mark.asyncio
async def test_fetch_statistics_empty_dataset() -> None:
    db = _empty_stats_session()
    now = datetime(2026, 8, 21, 12, 0, tzinfo=JERUSALEM)
    result = await fetch_management_statistics(db, "today", now=now)  # type: ignore[arg-type]
    assert isinstance(result, ManagementStatisticsResponse)
    assert result.kpis.granted_count == 0
    assert result.kpis.denied_count == 0
    assert result.kpis.approval_rate == 0.0
    assert result.daily_access == []
    assert result.denial_reasons == []
    assert result.access_methods.cash == 0
    assert result.operational.pending_card_topups == 0
    payload = result.model_dump(by_alias=True)
    assert "from" in payload
    assert payload["to"] is not None


@pytest.mark.asyncio
async def test_fetch_statistics_aggregates_mixed_rows() -> None:
    db = _FakeSession(
        {
            "SUM(fee_cents) FILTER": [
                SimpleNamespace(granted_count=8, denied_count=2, entrance_fee_total_cents=3500)
            ],
            "GROUP BY day": [
                SimpleNamespace(
                    day=date(2026, 8, 20),
                    granted_count=3,
                    denied_count=1,
                    entrance_fee_total_cents=1500,
                ),
                SimpleNamespace(
                    day=date(2026, 8, 21),
                    granted_count=5,
                    denied_count=1,
                    entrance_fee_total_cents=2000,
                ),
            ],
            "GROUP BY reason": [
                SimpleNamespace(reason="insufficient_balance", count=2),
                SimpleNamespace(reason="member_disabled", count=1),
            ],
            "payment_transactions": [
                SimpleNamespace(provider="cash", count=2),
                SimpleNamespace(provider="member", count=4),
                SimpleNamespace(provider="subscription", count=2),
            ],
            "product = 'monthly_subscription'": [
                SimpleNamespace(
                    paid_card_topup_count=3,
                    paid_card_topup_total_cents=15000,
                    paid_monthly_subscription_count=1,
                    paid_monthly_subscription_total_cents=9900,
                )
            ],
            "status IN ('pending', 'crediting')": [
                SimpleNamespace(pending_card_topups=2, crediting_card_topups=1)
            ],
            "fingerprints_service.members": [
                SimpleNamespace(enabled_member_count=10, disabled_member_count=2)
            ],
            "fingerprints_service.balances": [
                SimpleNamespace(total_member_balances_cents=42000)
            ],
            "FAILED', 'MANUAL_REVIEW": [SimpleNamespace(failed_access_attempts=1)],
            "door_operations": [SimpleNamespace(door_operation_failures=3)],
        }
    )
    now = datetime(2026, 8, 21, 18, 0, tzinfo=JERUSALEM)
    result = await fetch_management_statistics(db, "7d", now=now)  # type: ignore[arg-type]

    assert result.range == "7d"
    assert result.kpis.granted_count == 8
    assert result.kpis.denied_count == 2
    assert result.kpis.approval_rate == 0.8
    assert result.kpis.entrance_fee_total_cents == 3500
    assert result.kpis.paid_card_topup_count == 3
    assert result.kpis.paid_card_topup_total_cents == 15000
    assert result.kpis.paid_monthly_subscription_count == 1
    assert result.kpis.paid_monthly_subscription_total_cents == 9900
    assert result.kpis.enabled_member_count == 10
    assert result.kpis.disabled_member_count == 2
    assert result.kpis.total_member_balances_cents == 42000
    assert len(result.daily_access) == 2
    assert result.daily_access[0].date == "2026-08-20"
    assert result.denial_reasons[0].reason == "insufficient_balance"
    assert result.access_methods.cash == 2
    assert result.access_methods.member == 4
    assert result.access_methods.subscription == 2
    assert result.operational.pending_card_topups == 2
    assert result.operational.crediting_card_topups == 1
    assert result.operational.failed_access_attempts == 1
    assert result.operational.door_operation_failures == 3


@pytest.fixture
def stats_client() -> TestClient:
    app = FastAPI()

    @app.post("/management/auth", response_model=ManagementAuthResponse)
    async def auth(req: ManagementPinRequest, response: Response):
        return await authenticate_pin(req, response)

    @app.get(
        "/management/statistics",
        response_model=ManagementStatisticsResponse,
        dependencies=[Depends(require_management_token)],
    )
    async def statistics(range: str = "today"):
        if range not in VALID_RANGES:
            from fastapi import HTTPException, status

            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_range")
        db = _empty_stats_session()
        return await fetch_management_statistics(db, range)  # type: ignore[arg-type]

    return TestClient(app)


def test_statistics_requires_management_auth(stats_client: TestClient) -> None:
    denied = stats_client.get("/management/statistics")
    assert denied.status_code == 401

    login = stats_client.post("/management/auth", json={"pin": "test-pin-1234"})
    assert login.status_code == 200
    assert MANAGEMENT_COOKIE_NAME in stats_client.cookies

    allowed = stats_client.get("/management/statistics?range=today")
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["range"] == "today"
    assert body["timezone"] == "Asia/Jerusalem"
    assert "from" in body
    assert "to" in body
    assert body["kpis"]["granted_count"] == 0


def test_statistics_rejects_invalid_range(stats_client: TestClient) -> None:
    stats_client.post("/management/auth", json={"pin": "test-pin-1234"})
    response = stats_client.get("/management/statistics?range=custom")
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_range"
