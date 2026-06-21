"""Crawler <-> mapper integration (TASKS.md T6.2, PLAN.md §7).

``crawl_purchase_history`` accepts an optional ``mapper`` -- the isolated
DTO -> domain extension point. The application point is *per stitched
``ReceiptBundle``*: each bundle is passed through ``mapper.map`` before it lands
in ``CrawlResult.receipts``.

- ``mapper=None`` -> identity: the raw DTO bundles are returned unchanged.
- a custom mapper -> applied to every bundle, in order.

The crawler runs against a small fake Api double (scripted return values), with
the sleeper injected so no real time is used.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from novus_receipts.config import AppConfig
from novus_receipts.crawler.purchases_crawler import PurchasesCrawler
from novus_receipts.crawler.results import ReceiptBundle
from novus_receipts.dto.bill import BillResponse
from novus_receipts.dto.bonuses import UserBonusResponse
from novus_receipts.dto.purchases import (
    Purchase2Response,
    PurchaseOperationsDetails,
    PurchaseResponse,
)
from novus_receipts.mapping.mappers import IdentityMapper

# --------------------------------------------------------------------------- #
# Builders                                                                     #
# --------------------------------------------------------------------------- #


def make_check(*, check_number: str = "42") -> PurchaseResponse:
    return PurchaseResponse.model_validate(
        {
            "raw_end_time_stamp": "1718000000",
            "cash_id": 7,
            "shop_id": "123",
            "amount": "250.50",
            "bonus": "12.30",
            "check_number": check_number,
            "date": 1718000000,
            "shop_address": "Kyiv",
            "work_station_id": "9",
        }
    )


def make_page(
    months: list[list[PurchaseResponse]], *, page: int, total_count: int
) -> Purchase2Response:
    groups = [
        PurchaseOperationsDetails(month=202401 + i, amount="0", data=checks)
        for i, checks in enumerate(months)
    ]
    return Purchase2Response(data=groups, limit=10, page=page, total_count=total_count)


def make_bill(check_number: str = "42") -> BillResponse:
    return BillResponse.model_validate(
        {
            "id": 1,
            "amount": "250.50",
            "date": 1718000000,
            "check_number": check_number,
            "shop_id": "123",
            "shop_address": "Kyiv",
            "payment_method": "card",
            "bonuses_accrued": "0",
            "bonuses_written_off": "0",
            "bonuses_details": [],
            "discounts_details": [],
            "total_discount_saving": "0",
            "total_promotion_saving": "0",
            "coupons": [],
            "is_csat_available": False,
            "goods": [],
        }
    )


def make_bonuses() -> UserBonusResponse:
    return UserBonusResponse.model_validate({"data": "12.30", "data_long": 1230})


def make_config() -> AppConfig:
    return AppConfig(  # type: ignore[call-arg]
        user_token="tok-0",
        refresh_token="refresh-0",
        request_delay_s=0.2,
    )


class FakeApi:
    """Scripted stand-in for ``NovusApiClient`` (crawl-only surface)."""

    def __init__(self) -> None:
        self.access_token: str | None = "tok-0"
        self._purchases_2: list[Any] = []
        self._bill: list[Any] = []
        self._bonuses: list[Any] = []

    def queue_purchases_2(self, *items: Any) -> None:
        self._purchases_2.extend(items)

    def queue_bill(self, *items: Any) -> None:
        self._bill.extend(items)

    def queue_bonuses(self, *items: Any) -> None:
        self._bonuses.extend(items)

    @staticmethod
    def _next(queue: list[Any]) -> Any:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get_purchases_2(self, page: int = 1, limit: int | None = None) -> Any:
        return self._next(self._purchases_2)

    def get_bill(self, store: str, date: int, check_number: str, work_station_id: str) -> Any:
        return self._next(self._bill)

    def get_purchase(self, store: str, date: int, check_number: str) -> Any:
        raise AssertionError("fallback should not be reached in these tests")

    def get_current_bonuses(self) -> Any:
        return self._next(self._bonuses)

    def refresh_token(self, refresh_token: str) -> Any:  # pragma: no cover - unused here
        raise AssertionError("refresh should not be reached in these tests")

    def set_access_token(self, token: str) -> None:  # pragma: no cover - unused here
        self.access_token = token


# NOVUS_* env vars AppConfig reads; cleared so host env / a developer .env never
# leaks into these tests (which build configs explicitly).
_NOVUS_ENV_VARS = (
    "NOVUS_BASE_URL",
    "NOVUS_USER_TOKEN",
    "NOVUS_REFRESH_TOKEN",
    "NOVUS_PRIVATE_KEY",
    "NOVUS_PLATFORM_VERSION",
    "NOVUS_TIMEOUT_S",
    "NOVUS_MAX_RETRIES",
    "NOVUS_BACKOFF_BASE_S",
    "NOVUS_REQUEST_DELAY_S",
    "NOVUS_DETAIL_CONCURRENCY",
    "NOVUS_TIMEZONE",
    "NOVUS_PAGE_SIZE",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _NOVUS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _seed_two_receipts(api: FakeApi) -> None:
    c1 = make_check(check_number="1")
    c2 = make_check(check_number="2")
    api.queue_purchases_2(
        make_page([[c1, c2]], page=1, total_count=2),
        make_page([], page=2, total_count=2),
    )
    api.queue_bill(make_bill("1"), make_bill("2"))
    api.queue_bonuses(make_bonuses())


def make_crawler(api: FakeApi, sleeper: Callable[[float], None]) -> PurchasesCrawler:
    return PurchasesCrawler(api, make_config(), sleeper=sleeper)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# T6.2 mapper=None -> DTOs unchanged                                           #
# --------------------------------------------------------------------------- #


def test_mapper_none_returns_raw_bundles(fake_sleeper: Any) -> None:
    api = FakeApi()
    _seed_two_receipts(api)

    result = make_crawler(api, fake_sleeper).crawl_purchase_history(mapper=None)

    assert [b.summary.check_number for b in result.receipts] == ["1", "2"]
    assert all(isinstance(b, ReceiptBundle) for b in result.receipts)
    assert all(isinstance(b.detail, BillResponse) for b in result.receipts)


def test_identity_mapper_matches_no_mapper(fake_sleeper: Any) -> None:
    api_a = FakeApi()
    _seed_two_receipts(api_a)
    api_b = FakeApi()
    _seed_two_receipts(api_b)

    plain = make_crawler(api_a, fake_sleeper).crawl_purchase_history()
    identity = make_crawler(api_b, fake_sleeper).crawl_purchase_history(
        mapper=IdentityMapper()
    )

    assert [b.summary.check_number for b in plain.receipts] == [
        b.summary.check_number for b in identity.receipts
    ]
    assert all(isinstance(b, ReceiptBundle) for b in identity.receipts)


# --------------------------------------------------------------------------- #
# T6.2 custom mapper -> applied to each bundle                                 #
# --------------------------------------------------------------------------- #


class RecordingMapper:
    """A mapper that records every bundle and replaces its detail with ``None``."""

    def __init__(self) -> None:
        self.seen: list[ReceiptBundle] = []

    def map(self, dto: ReceiptBundle) -> ReceiptBundle:
        self.seen.append(dto)
        return ReceiptBundle(summary=dto.summary, detail=None)


def test_custom_mapper_applied_to_each_bundle_in_order(fake_sleeper: Any) -> None:
    api = FakeApi()
    _seed_two_receipts(api)
    mapper = RecordingMapper()

    result = make_crawler(api, fake_sleeper).crawl_purchase_history(mapper=mapper)

    # Mapper saw every stitched bundle, in crawl order, with its real detail.
    assert [b.summary.check_number for b in mapper.seen] == ["1", "2"]
    assert all(isinstance(b.detail, BillResponse) for b in mapper.seen)

    # The result holds the mapper's *output*, not the raw bundles.
    assert [b.summary.check_number for b in result.receipts] == ["1", "2"]
    assert all(b.detail is None for b in result.receipts)
