"""Unit tests for ChipCreateRequest national_id validation (no DB required)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import ChipCreateRequest


def test_create_request_accepts_valid_national_id() -> None:
    req = ChipCreateRequest(uid="FP-001", holder_name="Dana", national_id="123456782")
    assert req.national_id == "123456782"


def test_create_request_pads_and_validates() -> None:
    req = ChipCreateRequest(uid="FP-001", national_id="18")
    assert req.national_id == "000000018"


def test_create_request_rejects_invalid_national_id() -> None:
    with pytest.raises(ValidationError):
        ChipCreateRequest(uid="FP-001", national_id="123456789")


def test_create_request_allows_missing_national_id() -> None:
    req = ChipCreateRequest(uid="FP-001", holder_name="Dana")
    assert req.national_id is None
