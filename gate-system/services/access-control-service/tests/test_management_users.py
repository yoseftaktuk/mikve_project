"""Tests for management registered-user API models."""

from app.management import ManagementUserResponse


def test_management_user_response_uses_fingerprint_id() -> None:
    body = ManagementUserResponse(
        fingerprint_id="fp-1",
        uid="FP-001",
        holder_name="Dana",
        is_enabled=True,
        balance_cents=100,
    ).model_dump()
    assert body["fingerprint_id"] == "fp-1"
    assert "chip_id" not in body
