"""Tests for the bonus DTOs (TASKS.md T3.9, T3.10; PLAN.md §3.5).

Covers: balance (``UserBonusResponse``), a single bonus operation
(``BonusResponse`` with the ``type_alias`` -> ``alias`` mapping), the monthly
grouping (``BonusOperationResponse``), the history envelope (``BonusesResponse``),
the bonus-type reference (``BonusResponseType``) and the v2 reward envelope
(``RewardResponse``). Inputs are inline dicts mirroring NOVUS_API.md §4.3 plus a
uniquely-named JSON fixture for the nested-history case.
"""

from __future__ import annotations

from typing import Any

from novus_receipts.dto.bonuses import (
    BonusesResponse,
    BonusOperationResponse,
    BonusResponse,
    BonusResponseType,
    RewardResponse,
    UserBonusResponse,
)

# A representative single bonus operation as the API returns it (snake_case keys
# straight from NOVUS_API.md §4.3, including the diverging ``type_alias`` key).
_BONUS_JSON: dict[str, Any] = {
    "bonus": "12.50",
    "check_number": "42",
    "work_station_id": "7",
    "date": 1718000000,
    "is_authorization_bonus": False,
    "is_referral_bonus": True,
    "shop_address": "Kyiv, Khreshchatyk 1",
    "type_id": 3,
    "type_title": "Purchase bonus",
    "type_alias": "purchase",
    "purchase_id": 555,
    "cash_id": 9,
    "receipt_id": "rcpt-1",
    "shop_id": "123",
}


def test_user_bonus_response_deserializes_balance() -> None:
    model = UserBonusResponse.model_validate({"data": "100.00", "data_long": 10000})

    assert model.data == "100.00"
    assert model.data_long == 10000
    assert isinstance(model.data_long, int)


def test_user_bonus_response_ignores_unknown_keys() -> None:
    model = UserBonusResponse.model_validate(
        {"data": "5.00", "data_long": 500, "future_field": "x"}
    )

    assert model.model_dump() == {"data": "5.00", "data_long": 500}


def test_bonus_response_maps_type_alias_to_alias() -> None:
    model = BonusResponse.model_validate(_BONUS_JSON)

    # The diverging JSON key ``type_alias`` populates the ``alias`` field.
    assert model.alias == "purchase"


def test_bonus_response_full_field_types() -> None:
    model = BonusResponse.model_validate(_BONUS_JSON)

    assert model.bonus == "12.50"
    assert model.check_number == "42"
    assert model.work_station_id == "7"
    assert model.date == 1718000000
    assert isinstance(model.date, int)
    assert model.is_authorization_bonus is False
    assert model.is_referral_bonus is True
    assert model.shop_address == "Kyiv, Khreshchatyk 1"
    assert model.type_id == 3
    assert model.type_title == "Purchase bonus"
    assert model.purchase_id == 555
    assert model.cash_id == 9
    assert model.receipt_id == "rcpt-1"
    assert model.shop_id == "123"


def test_bonus_response_alias_round_trips_by_field_name() -> None:
    # populate_by_name=True (inherited from BaseDTO) accepts the Python name too.
    model = BonusResponse.model_validate({**_BONUS_JSON, "type_alias": "ref"})
    rebuilt = BonusResponse(
        bonus=model.bonus,
        check_number=model.check_number,
        work_station_id=model.work_station_id,
        date=model.date,
        is_authorization_bonus=model.is_authorization_bonus,
        is_referral_bonus=model.is_referral_bonus,
        shop_address=model.shop_address,
        type_id=model.type_id,
        type_title=model.type_title,
        alias="ref",
        purchase_id=model.purchase_id,
        cash_id=model.cash_id,
        receipt_id=model.receipt_id,
        shop_id=model.shop_id,
    )

    assert rebuilt.alias == "ref"


def test_bonus_operation_response_parses_nested_list() -> None:
    model = BonusOperationResponse.model_validate(
        {"month": 1717000000, "amount": "25.00", "data": [_BONUS_JSON, _BONUS_JSON]}
    )

    assert model.month == 1717000000
    assert model.amount == "25.00"
    assert len(model.data) == 2
    assert all(isinstance(item, BonusResponse) for item in model.data)
    assert model.data[0].alias == "purchase"


def test_bonuses_response_parses_envelope_and_nested_lists() -> None:
    model = BonusesResponse.model_validate(
        {
            "total_count": 2,
            "limit": 10,
            "data": [
                {"month": 1717000000, "amount": "25.00", "data": [_BONUS_JSON]},
                {"month": 1714000000, "amount": "5.00", "data": [_BONUS_JSON]},
            ],
        }
    )

    assert model.total_count == 2
    assert model.limit == 10
    assert len(model.data) == 2
    assert all(isinstance(op, BonusOperationResponse) for op in model.data)
    assert model.data[0].data[0].alias == "purchase"


def test_bonuses_response_from_fixture(
    load_fixture: Any,
) -> None:
    payload = load_fixture("bonuses_history_t310.json")
    model = BonusesResponse.model_validate(payload)

    assert model.total_count == 3
    assert model.limit == 10
    assert len(model.data) == 2
    first_month = model.data[0]
    assert first_month.month == 1717200000
    assert len(first_month.data) == 2
    # type_alias -> alias mapping survives a real JSON file too.
    assert first_month.data[0].alias == "purchase"
    assert first_month.data[1].alias == "referral"
    assert model.data[1].data[0].alias == "authorization"


def test_bonus_response_type_deserializes() -> None:
    model = BonusResponseType.model_validate(
        {"id": 3, "type_title": "Purchase bonus", "type_alias": "purchase"}
    )

    assert model.id == 3
    assert model.type_title == "Purchase bonus"
    # On the type reference the JSON key is ``type_alias`` and stays as such.
    assert model.type_alias == "purchase"


def test_reward_response_v2_parses_same_nested_lists() -> None:
    model = RewardResponse.model_validate(
        {
            "data": [
                {"month": 1717000000, "amount": "25.00", "data": [_BONUS_JSON]},
            ],
            "limit": 10,
            "total_count": 1,
        }
    )

    assert model.limit == 10
    assert model.total_count == 1
    assert len(model.data) == 1
    assert isinstance(model.data[0], BonusOperationResponse)
    assert model.data[0].data[0].alias == "purchase"
