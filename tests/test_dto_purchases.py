"""Tests for the purchase-history list DTOs (TASKS.md T3.3-T3.6, PLAN.md §3.3).

Each test deserializes a representative JSON dict (built inline, mirroring
NOVUS_API.md §4.1) and asserts the alias mapping, field types and nesting.
"""

from __future__ import annotations

from novus_receipts.dto.purchases import (
    Purchase2Response,
    PurchaseDetailsResponse,
    PurchaseOperationsDetails,
    PurchaseResponse,
    ShoppingDetailsResponse,
    ShoppingMonthSummary,
    ShoppingResponse,
)


def _purchase_json() -> dict[str, object]:
    """A single receipt as returned inside the list endpoints (variants A/B)."""

    return {
        "raw_end_time_stamp": "1718000000",
        "cash_id": 7,
        "shop_id": "123",
        "amount": "250.50",
        "bonus": "12.30",
        "check_number": "42",
        "date": 1718000000,
        "shop_address": "Kyiv, Khreshchatyk 1",
        "work_station_id": "9",
    }


def _shopping_json() -> dict[str, object]:
    """A single receipt as returned inside ``/v2/user/purchases`` (variant C)."""

    return {
        "id": 555,
        "amount": "99.99",
        "bonus": "1.00",
        "cash_id": 3,
        "check_number": "100",
        "date": 1718111111,
        "shop_address": "Lviv, Rynok 5",
        "shop_id": "321",
        "work_station_id": "2",
    }


# --- T3.3: PurchaseResponse ------------------------------------------------


def test_purchase_response_alias_maps_raw_end_time_stamp_to_id() -> None:
    model = PurchaseResponse.model_validate(_purchase_json())

    assert model.id == "1718000000"


def test_purchase_response_field_types() -> None:
    model = PurchaseResponse.model_validate(_purchase_json())

    assert isinstance(model.id, str)
    assert isinstance(model.cash_id, int)
    assert isinstance(model.shop_id, str)
    assert isinstance(model.amount, str)
    assert isinstance(model.bonus, str)
    assert isinstance(model.check_number, str)
    assert isinstance(model.date, int)
    assert isinstance(model.shop_address, str)
    assert isinstance(model.work_station_id, str)


def test_purchase_response_values_roundtrip() -> None:
    model = PurchaseResponse.model_validate(_purchase_json())

    assert model.cash_id == 7
    assert model.shop_id == "123"
    assert model.amount == "250.50"
    assert model.date == 1718000000
    assert model.work_station_id == "9"


def test_purchase_response_parses_live_minimal_item() -> None:
    # The real /user/purchases_2 item omits raw_end_time_stamp, cash_id and
    # work_station_id; those must be optional (None) so the list still parses.
    live_item = {
        "shop_id": "7016",
        "shop_address": "м. Київ, Львівська площа, 8Б",
        "date": 1781690422,
        "bonus": "2.32",
        "amount": "232.45",
        "check_number": "1118.29-0",
    }

    model = PurchaseResponse.model_validate(live_item)

    assert model.id is None
    assert model.cash_id is None
    assert model.work_station_id is None
    assert model.shop_id == "7016"
    assert model.date == 1781690422
    assert model.check_number == "1118.29-0"


def test_purchase_response_ignores_unknown_keys() -> None:
    payload = _purchase_json()
    payload["undocumented_field"] = {"nested": True}

    model = PurchaseResponse.model_validate(payload)

    assert model.id == "1718000000"
    assert not hasattr(model, "undocumented_field")


def test_purchase_response_populate_by_name() -> None:
    model = PurchaseResponse(
        id="ts-by-name",
        cash_id=1,
        shop_id="s",
        amount="0",
        bonus="0",
        check_number="n",
        date=0,
        shop_address="addr",
        work_station_id="w",
    )

    assert model.id == "ts-by-name"


# --- T3.4: PurchaseOperationsDetails + PurchaseDetailsResponse (A) ----------


def test_purchase_operations_details_nests_receipts() -> None:
    payload = {
        "month": 1718000000,
        "amount": "500.00",
        "data": [_purchase_json(), _purchase_json()],
    }

    model = PurchaseOperationsDetails.model_validate(payload)

    assert isinstance(model.month, int)
    assert model.amount == "500.00"
    assert len(model.data) == 2
    assert all(isinstance(item, PurchaseResponse) for item in model.data)
    assert model.data[0].id == "1718000000"


def test_purchase_details_response_has_more_and_nesting() -> None:
    payload = {
        "data": [
            {
                "month": 1718000000,
                "amount": "500.00",
                "data": [_purchase_json()],
            }
        ],
        "has_more": True,
    }

    model = PurchaseDetailsResponse.model_validate(payload)

    assert model.has_more is True
    assert isinstance(model.has_more, bool)
    assert len(model.data) == 1
    assert isinstance(model.data[0], PurchaseOperationsDetails)
    assert model.data[0].data[0].shop_id == "123"


def test_purchase_details_response_empty_data() -> None:
    model = PurchaseDetailsResponse.model_validate({"data": [], "has_more": False})

    assert model.data == []
    assert model.has_more is False


# --- T3.5: Purchase2Response (B) -------------------------------------------


def test_purchase2_response_pagination_fields() -> None:
    payload = {
        "data": [
            {
                "month": 1718000000,
                "amount": "500.00",
                "data": [_purchase_json()],
            }
        ],
        "limit": 10,
        "page": 1,
        "total_count": 37,
    }

    model = Purchase2Response.model_validate(payload)

    assert isinstance(model.limit, int)
    assert isinstance(model.page, int)
    assert isinstance(model.total_count, int)
    assert model.limit == 10
    assert model.page == 1
    assert model.total_count == 37
    assert model.data[0].data[0].id == "1718000000"


# --- T3.6: Shopping models (C) ---------------------------------------------


def test_shopping_response_id_is_int() -> None:
    model = ShoppingResponse.model_validate(_shopping_json())

    assert model.id == 555
    assert isinstance(model.id, int)


def test_shopping_response_field_types() -> None:
    model = ShoppingResponse.model_validate(_shopping_json())

    assert isinstance(model.amount, str)
    assert isinstance(model.bonus, str)
    assert isinstance(model.cash_id, int)
    assert isinstance(model.check_number, str)
    assert isinstance(model.date, int)
    assert isinstance(model.shop_address, str)
    assert isinstance(model.shop_id, str)
    assert isinstance(model.work_station_id, str)


def test_shopping_month_summary_parses_bonuses_accured() -> None:
    payload = {
        "month": 1718000000,
        "amount": "600.00",
        "bonuses_accured": "15.75",
        "discounts": "8.20",
        "data": [_shopping_json()],
    }

    model = ShoppingMonthSummary.model_validate(payload)

    assert model.bonuses_accured == "15.75"
    assert isinstance(model.bonuses_accured, str)
    assert model.discounts == "8.20"
    assert isinstance(model.month, int)
    assert len(model.data) == 1
    assert isinstance(model.data[0], ShoppingResponse)
    assert model.data[0].id == 555


def test_shopping_details_response_full_shape() -> None:
    payload = {
        "data": [
            {
                "month": 1718000000,
                "amount": "600.00",
                "bonuses_accured": "15.75",
                "discounts": "8.20",
                "data": [_shopping_json()],
            }
        ],
        "has_more": False,
        "limit": 10,
        "page": 2,
        "total_count": 12,
    }

    model = ShoppingDetailsResponse.model_validate(payload)

    assert isinstance(model.has_more, bool)
    assert model.has_more is False
    assert model.limit == 10
    assert model.page == 2
    assert model.total_count == 12
    assert len(model.data) == 1
    assert isinstance(model.data[0], ShoppingMonthSummary)
    assert model.data[0].bonuses_accured == "15.75"
    assert model.data[0].data[0].shop_id == "321"
