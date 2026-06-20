"""Tests for the envelope and auth DTOs (TASKS.md T3.1, T3.2; PLAN.md §3.1/§3.2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from novus_receipts.dto._base import BaseDTO
from novus_receipts.dto.auth import ConfirmWithOtpResponse
from novus_receipts.dto.envelope import Data


class _Item(BaseDTO):
    """Small concrete payload used to parameterise the generic envelope."""

    id: int
    title: str


# --------------------------------------------------------------------------- #
# T3.1 — Data[T] envelope
# --------------------------------------------------------------------------- #


def test_data_parses_full_envelope_with_concrete_model() -> None:
    model = Data[_Item].model_validate(
        {
            "code": 200,
            "data": {"id": 7, "title": "milk"},
            "message": "ok",
            "has_more": True,
        }
    )

    assert model.code == 200
    assert isinstance(model.data, _Item)
    assert model.data.id == 7
    assert model.data.title == "milk"
    assert model.message == "ok"
    assert model.has_more is True


def test_data_message_and_has_more_are_optional() -> None:
    model = Data[_Item].model_validate(
        {"code": 0, "data": {"id": 1, "title": "bread"}}
    )

    assert model.code == 0
    assert model.data == _Item(id=1, title="bread")
    assert model.message is None
    assert model.has_more is None


def test_data_parses_list_payload() -> None:
    model = Data[list[_Item]].model_validate(
        {
            "code": 200,
            "data": [
                {"id": 1, "title": "a"},
                {"id": 2, "title": "b"},
            ],
            "has_more": False,
        }
    )

    assert [item.id for item in model.data] == [1, 2]
    assert all(isinstance(item, _Item) for item in model.data)
    assert model.has_more is False
    assert model.message is None


def test_data_ignores_unknown_keys() -> None:
    model = Data[_Item].model_validate(
        {
            "code": 200,
            "data": {"id": 3, "title": "eggs", "surprise": "x"},
            "unexpected_envelope_key": 123,
        }
    )

    assert model.data == _Item(id=3, title="eggs")
    assert not hasattr(model, "unexpected_envelope_key")


def test_data_requires_code_and_data() -> None:
    with pytest.raises(ValidationError):
        Data[_Item].model_validate({"message": "missing code and data"})


# --------------------------------------------------------------------------- #
# T3.2 — ConfirmWithOtpResponse (also /auth/refresh_token)
# --------------------------------------------------------------------------- #


def test_confirm_with_otp_response_parses_all_fields() -> None:
    payload = {
        "token": "access-abc",
        "refresh_token": "refresh-xyz",
        "first_authorization": True,
        "user_first_name": "Roman",
        "bonuses": "150.00",
        "bonus_type": "standard",
        "blocked_card": False,
    }

    model = ConfirmWithOtpResponse.model_validate(payload)

    assert model.token == "access-abc"
    assert model.refresh_token == "refresh-xyz"
    assert model.first_authorization is True
    assert model.user_first_name == "Roman"
    assert model.bonuses == "150.00"
    assert model.bonus_type == "standard"
    assert model.blocked_card is False


def test_confirm_with_otp_response_money_field_stays_str() -> None:
    model = ConfirmWithOtpResponse.model_validate(
        {
            "token": "t",
            "refresh_token": "r",
            "first_authorization": False,
            "user_first_name": "Ann",
            "bonuses": "0.00",
            "bonus_type": "",
            "blocked_card": True,
        }
    )

    assert isinstance(model.bonuses, str)
    assert model.bonuses == "0.00"
    assert model.blocked_card is True


def test_confirm_with_otp_response_ignores_unknown_keys() -> None:
    model = ConfirmWithOtpResponse.model_validate(
        {
            "token": "t",
            "refresh_token": "r",
            "first_authorization": False,
            "user_first_name": "Ann",
            "bonuses": "0.00",
            "bonus_type": "standard",
            "blocked_card": False,
            "expires_in": 3600,  # undocumented key must be ignored
        }
    )

    assert model.token == "t"
    assert not hasattr(model, "expires_in")


def test_confirm_with_otp_response_requires_refresh_token() -> None:
    with pytest.raises(ValidationError):
        ConfirmWithOtpResponse.model_validate(
            {
                "token": "t",
                "first_authorization": False,
                "user_first_name": "Ann",
                "bonuses": "0.00",
                "bonus_type": "standard",
                "blocked_card": False,
            }
        )
