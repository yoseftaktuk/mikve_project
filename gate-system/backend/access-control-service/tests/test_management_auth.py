"""Tests for management PIN auth via HttpOnly cookies."""

from __future__ import annotations

import time

import pytest
from fastapi import Depends, FastAPI, Request, Response
from fastapi.testclient import TestClient

from app import management as mgmt
from app.management import (
    MANAGEMENT_COOKIE_NAME,
    ManagementAuthResponse,
    ManagementPinRequest,
    ManagementSessionResponse,
    TOKEN_TTL_SECONDS,
    authenticate_pin,
    get_management_session,
    logout_management,
    require_management_token,
)
from app.settings import management_cookie_secure_enabled, settings


@pytest.fixture(autouse=True)
def _reset_tokens_and_pin(monkeypatch: pytest.MonkeyPatch):
    """Isolate in-memory tokens and ensure a known management PIN for each test."""
    mgmt._mgmt_tokens.clear()
    monkeypatch.setattr(settings, "management_pin", "test-pin-1234")
    monkeypatch.setattr(settings, "environment", "dev")
    monkeypatch.setattr(settings, "management_cookie_secure", None)
    yield
    mgmt._mgmt_tokens.clear()


@pytest.fixture
def client() -> TestClient:
    """Minimal FastAPI app exposing management auth routes only."""
    app = FastAPI()

    @app.post("/management/auth", response_model=ManagementAuthResponse)
    async def auth(req: ManagementPinRequest, response: Response):
        return await authenticate_pin(req, response)

    @app.get("/management/session", response_model=ManagementSessionResponse)
    async def session(request: Request):
        return get_management_session(request)

    @app.post("/management/logout", response_model=ManagementSessionResponse)
    async def logout(request: Request, response: Response):
        return await logout_management(request, response)

    @app.get("/management/protected", dependencies=[Depends(require_management_token)])
    async def protected():
        return {"ok": True}

    return TestClient(app)


def _cookie_header(response) -> str:
    return response.headers.get("set-cookie", "")


def test_login_sets_httponly_cookie_without_token_in_body(client: TestClient) -> None:
    response = client.post("/management/auth", json={"pin": "test-pin-1234"})
    assert response.status_code == 200
    body = response.json()
    assert body == {"authenticated": True}
    assert "token" not in body

    set_cookie = _cookie_header(response)
    assert f"{MANAGEMENT_COOKIE_NAME}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert f"Max-Age={TOKEN_TTL_SECONDS}" in set_cookie
    # ENVIRONMENT=dev → Secure should be off
    assert "Secure" not in set_cookie


def test_login_rejects_invalid_pin(client: TestClient) -> None:
    response = client.post("/management/auth", json={"pin": "wrong"})
    assert response.status_code == 401
    assert MANAGEMENT_COOKIE_NAME not in client.cookies


def test_protected_route_requires_cookie(client: TestClient) -> None:
    denied = client.get("/management/protected")
    assert denied.status_code == 401

    login = client.post("/management/auth", json={"pin": "test-pin-1234"})
    assert login.status_code == 200
    allowed = client.get("/management/protected")
    assert allowed.status_code == 200
    assert allowed.json() == {"ok": True}


def test_session_endpoint_reflects_auth_state(client: TestClient) -> None:
    before = client.get("/management/session")
    assert before.status_code == 200
    assert before.json() == {"authenticated": False}

    client.post("/management/auth", json={"pin": "test-pin-1234"})
    after = client.get("/management/session")
    assert after.status_code == 200
    assert after.json() == {"authenticated": True}


def test_logout_clears_cookie_and_revokes_token(client: TestClient) -> None:
    client.post("/management/auth", json={"pin": "test-pin-1234"})
    assert client.get("/management/protected").status_code == 200

    logout = client.post("/management/logout")
    assert logout.status_code == 200
    assert logout.json() == {"authenticated": False}
    set_cookie = _cookie_header(logout)
    assert f"{MANAGEMENT_COOKIE_NAME}=" in set_cookie
    assert "Max-Age=0" in set_cookie
    assert "HttpOnly" in set_cookie

    assert client.get("/management/protected").status_code == 401
    assert client.get("/management/session").json() == {"authenticated": False}


def test_invalid_cookie_is_rejected(client: TestClient) -> None:
    client.cookies.set(MANAGEMENT_COOKIE_NAME, "not-a-real-token")
    response = client.get("/management/protected")
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_token"


def test_expired_cookie_is_rejected(client: TestClient) -> None:
    token = mgmt.create_management_token()
    mgmt._mgmt_tokens[token] = time.time() - 1
    client.cookies.set(MANAGEMENT_COOKIE_NAME, token)
    response = client.get("/management/protected")
    assert response.status_code == 401
    assert response.json()["detail"] == "token_expired"


def test_secure_flag_enabled_outside_dev(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "management_cookie_secure", None)
    assert management_cookie_secure_enabled() is True

    response = client.post("/management/auth", json={"pin": "test-pin-1234"})
    assert response.status_code == 200
    assert "Secure" in _cookie_header(response)


def test_secure_flag_override(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setattr(settings, "environment", "dev")
    monkeypatch.setattr(settings, "management_cookie_secure", True)
    assert management_cookie_secure_enabled() is True

    response = client.post("/management/auth", json={"pin": "test-pin-1234"})
    assert "Secure" in _cookie_header(response)
