"""MemberValidation parses the fingerprints-service validate payload."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.clients import MemberValidation


def _valid_payload() -> dict:
    return {
        "member_id": "11111111-1111-1111-1111-111111111111",
        "uid": "FP-001",
        "is_enabled": True,
        "balance_cents": 2000,
        "holder_name": "דנה",
        "subscription_active": False,
        "subscription_month_name": None,
        "subscription_free_entry_available_today": False,
        "current_hebrew_month_name": "אב",
        "assigned_user_id": None,
        "national_id": "123456782",
    }


def test_model_validate_accepts_validate_payload() -> None:
    member = MemberValidation.model_validate(_valid_payload())
    assert member.member_id == "11111111-1111-1111-1111-111111111111"
    assert member.uid == "FP-001"
    assert member.is_enabled is True
    assert member.balance_cents == 2000
    assert member.holder_name == "דנה"
    assert member.subscription_active is False
    assert member.current_hebrew_month_name == "אב"


def test_model_validate_rejects_bad_types() -> None:
    bad_balance = _valid_payload()
    bad_balance["balance_cents"] = "twelve"
    with pytest.raises(ValidationError):
        MemberValidation.model_validate(bad_balance)

    bad_enabled = _valid_payload()
    bad_enabled["is_enabled"] = "not-a-bool"
    with pytest.raises(ValidationError):
        MemberValidation.model_validate(bad_enabled)
