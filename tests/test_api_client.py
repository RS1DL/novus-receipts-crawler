"""Tests for :class:`NovusApiClient` (TASKS.md T4.0-T4.12).

Every test drives the client through ``httpx.MockTransport`` (via the
``make_mock_client`` fixture) and asserts on the *outgoing* request -- method,
path, query and headers -- plus that a canned JSON response deserialises into
the right DTO. No real network or time is used.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
import pytest

from novus_receipts.api.client import NovusApiClient
from novus_receipts.config import AppConfig
from novus_receipts.dto.auth import ConfirmWithOtpResponse
from novus_receipts.dto.bill import BillResponse, PurchaseDetalizationResponse
from novus_receipts.dto.bonuses import (
    BonusesResponse,
    UserBonusResponse,
)
from novus_receipts.dto.envelope import Data
from novus_receipts.dto.purchases import (
    Purchase2Response,
    PurchaseDetailsResponse,
    ShoppingDetailsResponse,
)
from novus_receipts.errors import NovusAuthError, NovusTransientError
from tests.conftest import (
    BASE_URL,
    PRIVATE_KEY,
    RequestRecorder,
    assert_constant_headers,
    query_of,
)


def make_config(**overrides: object) -> AppConfig:
    """Build an ``AppConfig`` with a fixed token for the client under test."""

    fields: dict[str, object] = {
        "user_token": "start-token",
        "base_url": BASE_URL,
        "private_key": PRIVATE_KEY,
        "platform": "android",
        "platform_version": "14 (34)",
    }
    fields.update(overrides)
    return AppConfig(**fields)  # type: ignore[arg-type]


def build_client(
    make_mock_client: Callable[..., httpx.Client],
    *,
    json_data: object = None,
    status_code: int = 200,
    config: AppConfig | None = None,
) -> tuple[NovusApiClient, RequestRecorder]:
    """Wire a recorder-backed ``httpx.Client`` into a ``NovusApiClient``."""

    cfg = config if config is not None else make_config()
    recorder = RequestRecorder(json_data, status_code)
    http = make_mock_client(recorder, base_url=cfg.base_url)
    return NovusApiClient(http, cfg), recorder


# --- T4.0: constructor + constant headers -----------------------------------


def test_constant_headers_on_every_request(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(make_mock_client, json_data=_bonuses_types_payload())

    client.get_bonuses_types()

    assert_constant_headers(recorder.last)


def test_constructor_does_not_set_user_token_header_globally(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(make_mock_client, json_data=_bonuses_types_payload())

    # A non-auth call must not carry user_token even though a token is configured.
    client.get_bonuses_types()

    assert "user_token" not in recorder.last.headers


# --- T4.1: access token property / setter -----------------------------------


def test_access_token_starts_from_config_token(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, _ = build_client(make_mock_client)

    assert client.access_token == "start-token"


def test_set_access_token_updates_property_and_auth_header(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data=_user_bonus_payload()
    )

    client.set_access_token("rotated-token")
    assert client.access_token == "rotated-token"

    client.get_current_bonuses()
    assert recorder.last.headers["user_token"] == "rotated-token"


def test_auth_method_sends_user_token(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data=_user_bonus_payload()
    )

    client.get_current_bonuses()

    assert recorder.last.headers["user_token"] == "start-token"
    assert_constant_headers(recorder.last)


def test_auth_method_without_token_omits_user_token_header(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    # T4.1: "без токена — нема заголовка". When no access token is held, an
    # authenticated method still issues the request, just without the user_token
    # header (NOVUS_API.md §1.3: an empty stored token => the header is omitted).
    client, recorder = build_client(
        make_mock_client, json_data=_user_bonus_payload()
    )
    client._access_token = None  # no public None-setter; clear it directly

    client.get_current_bonuses()

    assert client.access_token is None
    assert "user_token" not in recorder.last.headers
    assert_constant_headers(recorder.last)


# --- T4.2: classify_response integration ------------------------------------


def test_401_surfaces_auth_error(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, _ = build_client(
        make_mock_client, json_data={"message": "expired"}, status_code=401
    )

    with pytest.raises(NovusAuthError):
        client.get_current_bonuses()


def test_500_surfaces_transient_error(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, _ = build_client(
        make_mock_client, json_data={"message": "boom"}, status_code=500
    )

    with pytest.raises(NovusTransientError):
        client.get_current_bonuses()


def test_error_checked_before_parsing(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    # An empty/non-DTO body on a 401 must raise the auth error, not a
    # validation error -- proving raise_for_response runs before model_validate.
    client, _ = build_client(make_mock_client, json_data=None, status_code=401)

    with pytest.raises(NovusAuthError):
        client.get_profile()


# --- T4.3: refresh_token ----------------------------------------------------


def _confirm_payload() -> dict[str, object]:
    return {
        "token": "new-access",
        "refresh_token": "new-refresh",
        "first_authorization": False,
        "user_first_name": "Ann",
        "bonuses": "10.00",
        "bonus_type": "standard",
        "blocked_card": False,
    }


def test_refresh_token_request_shape(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data=_confirm_payload()
    )

    result = client.refresh_token("the-refresh-token")

    req = recorder.last
    assert req.method == "POST"
    assert urlsplit(str(req.url)).path == "/auth/refresh_token"
    assert "user_token" not in req.headers
    assert_constant_headers(req)
    assert json.loads(req.content) == {"refresh_token": "the-refresh-token"}
    assert isinstance(result, ConfirmWithOtpResponse)
    assert result.token == "new-access"
    assert result.refresh_token == "new-refresh"


# --- T4.4: get_purchases ----------------------------------------------------


def _purchase_response_item() -> dict[str, object]:
    return {
        "raw_end_time_stamp": "1718000000",
        "cash_id": 7,
        "shop_id": "123",
        "amount": "100.00",
        "bonus": "5.00",
        "check_number": "42",
        "date": 1718000000,
        "shop_address": "Kyiv",
        "work_station_id": "9",
    }


def _purchase_details_payload() -> dict[str, object]:
    return {
        "data": [
            {
                "month": 1718000000,
                "amount": "100.00",
                "data": [_purchase_response_item()],
            }
        ],
        "has_more": True,
    }


def test_get_purchases_request_and_dto(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data=_purchase_details_payload()
    )

    result = client.get_purchases(page=3)

    req = recorder.last
    assert req.method == "GET"
    assert urlsplit(str(req.url)).path == "/user/purchases"
    assert query_of(req) == {"page": ["3"]}
    assert req.headers["user_token"] == "start-token"
    assert isinstance(result, PurchaseDetailsResponse)
    assert result.has_more is True
    assert result.data[0].data[0].id == "1718000000"


def test_get_purchases_defaults_page_1(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data=_purchase_details_payload()
    )

    client.get_purchases()

    assert query_of(recorder.last) == {"page": ["1"]}


# --- T4.5: get_purchases_2 --------------------------------------------------


def _purchase2_payload() -> dict[str, object]:
    return {
        "data": [
            {
                "month": 1718000000,
                "amount": "100.00",
                "data": [_purchase_response_item()],
            }
        ],
        "limit": 10,
        "page": 2,
        "total_count": 1,
    }


def test_get_purchases_2_request_and_dto(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data=_purchase2_payload()
    )

    result = client.get_purchases_2(page=2)

    req = recorder.last
    assert req.method == "GET"
    assert urlsplit(str(req.url)).path == "/user/purchases_2"
    # No limit sent by default -> server's default page size applies.
    assert query_of(req) == {"page": ["2"]}
    assert req.headers["user_token"] == "start-token"
    assert isinstance(result, Purchase2Response)
    assert result.total_count == 1
    assert result.page == 2


def test_get_purchases_2_sends_limit_when_given(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data=_purchase2_payload()
    )

    client.get_purchases_2(page=1, limit=200)

    assert query_of(recorder.last) == {"page": ["1"], "limit": ["200"]}


# --- T4.6: get_shopping -----------------------------------------------------


def _shopping_payload() -> dict[str, object]:
    return {
        "data": [
            {
                "month": 1718000000,
                "amount": "100.00",
                "bonuses_accured": "5.00",
                "discounts": "1.00",
                "data": [
                    {
                        "id": 555,
                        "amount": "100.00",
                        "bonus": "5.00",
                        "cash_id": 7,
                        "check_number": "42",
                        "date": 1718000000,
                        "shop_address": "Kyiv",
                        "shop_id": "123",
                        "work_station_id": "9",
                    }
                ],
            }
        ],
        "has_more": False,
        "limit": 10,
        "page": 1,
        "total_count": 1,
    }


def test_get_shopping_request_and_dto(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data=_shopping_payload()
    )

    result = client.get_shopping(page=4, limit=25)

    req = recorder.last
    assert req.method == "GET"
    assert urlsplit(str(req.url)).path == "/v2/user/purchases"
    assert query_of(req) == {"page": ["4"], "limit": ["25"]}
    assert req.headers["user_token"] == "start-token"
    assert isinstance(result, ShoppingDetailsResponse)
    assert result.data[0].data[0].id == 555


def test_get_shopping_defaults(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data=_shopping_payload()
    )

    client.get_shopping()

    assert query_of(recorder.last) == {"page": ["1"], "limit": ["10"]}


# --- T4.7: get_purchase -----------------------------------------------------


def _detalization_payload() -> dict[str, object]:
    return {
        "id": 1,
        "amount": "100.00",
        "bonuses_accrued": "5.00",
        "bonuses_written_off": "0.00",
        "check_number": "42",
        "coupons": [],
        "date": 1718000000,
        "goods": [
            {
                "title": "Milk",
                "amount": "30.00",
                "quantity": "1",
                "price_type": "unit",
                "id": 10,
            }
        ],
        "payment_method": "card",
        "shop_address": "Kyiv",
        "shop_id": "123",
    }


def test_get_purchase_request_and_dto(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data=_detalization_payload()
    )

    result = client.get_purchase(store="123", date=1718000000, check_number="42")

    req = recorder.last
    assert req.method == "GET"
    assert urlsplit(str(req.url)).path == "/user/purchase_2"
    assert query_of(req) == {
        "store": ["123"],
        "date": ["1718000000"],
        "check_number": ["42"],
    }
    assert req.headers["user_token"] == "start-token"
    assert isinstance(result, PurchaseDetalizationResponse)
    assert result.goods[0].title == "Milk"


# --- T4.8: get_bill ---------------------------------------------------------


def _bill_payload() -> dict[str, object]:
    return {
        "id": 1,
        "amount": "100.00",
        "date": 1718000000,
        "check_number": "42",
        "shop_id": "123",
        "shop_address": "Kyiv",
        "payment_method": "card",
        "bonuses_accrued": "5.00",
        "bonuses_written_off": "0.00",
        "bonuses_details": [],
        "discounts_details": [],
        "total_discount_saving": "0.00",
        "total_promotion_saving": "0.00",
        "coupons": [],
        "is_csat_available": False,
        "goods": [],
    }


def test_get_bill_request_and_dto(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(make_mock_client, json_data=_bill_payload())

    result = client.get_bill(
        store="123", date=1718000000, check_number="42", work_station_id="9"
    )

    req = recorder.last
    assert req.method == "GET"
    assert urlsplit(str(req.url)).path == "/v2/user/purchase"
    assert query_of(req) == {
        "store": ["123"],
        "date": ["1718000000"],
        "check_number": ["42"],
        "work_station_id": ["9"],
    }
    assert req.headers["user_token"] == "start-token"
    assert isinstance(result, BillResponse)
    assert result.id == 1


# --- T4.9: get_current_bonuses ----------------------------------------------


def _user_bonus_payload() -> dict[str, object]:
    return {"data": "123.45", "data_long": 12345}


def test_get_current_bonuses_request_and_dto(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data=_user_bonus_payload()
    )

    result = client.get_current_bonuses()

    req = recorder.last
    assert req.method == "GET"
    assert urlsplit(str(req.url)).path == "/user/bonuses/current"
    assert query_of(req) == {}
    assert req.headers["user_token"] == "start-token"
    assert isinstance(result, UserBonusResponse)
    assert result.data_long == 12345


# --- T4.10: get_bonuses_history ---------------------------------------------


def _bonuses_history_payload() -> dict[str, object]:
    return {
        "total_count": 1,
        "limit": 10,
        "data": [
            {
                "month": 1718000000,
                "amount": "5.00",
                "data": [
                    {
                        "bonus": "5.00",
                        "check_number": "42",
                        "work_station_id": "9",
                        "date": 1718000000,
                        "is_authorization_bonus": False,
                        "is_referral_bonus": False,
                        "shop_address": "Kyiv",
                        "type_id": 1,
                        "type_title": "Purchase",
                        "type_alias": "purchase",
                        "purchase_id": 100,
                        "cash_id": 7,
                        "receipt_id": "r1",
                        "shop_id": "123",
                    }
                ],
            }
        ],
    }


def test_get_bonuses_history_request_and_dto(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data=_bonuses_history_payload()
    )

    result = client.get_bonuses_history(page=2, limit=20, type_id=3)

    req = recorder.last
    assert req.method == "GET"
    assert urlsplit(str(req.url)).path == "/user/bonuses"
    assert query_of(req) == {
        "page": ["2"],
        "limit": ["20"],
        "type_id": ["3"],
    }
    assert req.headers["user_token"] == "start-token"
    assert isinstance(result, BonusesResponse)
    assert result.data[0].data[0].alias == "purchase"


def test_get_bonuses_history_omits_type_id_when_none(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data=_bonuses_history_payload()
    )

    client.get_bonuses_history()

    q = query_of(recorder.last)
    assert "type_id" not in q
    assert q == {"page": ["1"], "limit": ["10"]}


# --- T4.11: get_bonuses_types -----------------------------------------------


def _bonuses_types_payload() -> dict[str, object]:
    return {
        "code": 0,
        "data": [
            {"id": 1, "type_title": "Purchase", "type_alias": "purchase"},
            {"id": 2, "type_title": "Referral", "type_alias": "referral"},
        ],
        "message": None,
        "has_more": None,
    }


def test_get_bonuses_types_no_auth_header_and_dto(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data=_bonuses_types_payload()
    )

    result = client.get_bonuses_types()

    req = recorder.last
    assert req.method == "GET"
    assert urlsplit(str(req.url)).path == "/user/bonuses_types"
    assert query_of(req) == {}
    assert "user_token" not in req.headers
    assert_constant_headers(req)
    assert isinstance(result, Data)
    assert result.code == 0
    assert result.data[0].type_alias == "purchase"
    assert result.data[1].id == 2


# --- T4.12: get_profile -----------------------------------------------------


def test_get_profile_request_and_dto(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_client(
        make_mock_client, json_data={"name": "Ann", "id": 42}
    )

    result = client.get_profile()

    req = recorder.last
    assert req.method == "GET"
    assert urlsplit(str(req.url)).path == "/user/profile"
    assert query_of(req) == {}
    assert req.headers["user_token"] == "start-token"
    # ProfileResponse uses extra="allow"; unknown fields are retained.
    assert result.model_dump()["name"] == "Ann"
