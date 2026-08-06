"""Unit tests for fingerprints-service request schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import ChipUpdateRequest


def test_chip_update_accepts_name_only():
    req = ChipUpdateRequest(holder_name="דנה")
    assert req.holder_name == "דנה"
    assert req.is_enabled is None


def test_chip_update_accepts_enabled_only():
    req = ChipUpdateRequest(is_enabled=False)
    assert req.is_enabled is False
    assert req.holder_name is None


def test_chip_update_rejects_overlong_name():
    with pytest.raises(ValidationError):
        ChipUpdateRequest(holder_name="x" * 81)
