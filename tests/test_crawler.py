"""Tests for the purchase crawler (TASKS.md T5.1-T5.10, PLAN.md §4).

The crawler is exercised against a *fake* Api double (a small in-test class with
scripted return values / raised exceptions) rather than real HTTP. The sleeper
is injected (``fake_sleeper`` fixture) so retry backoff and the polite
inter-detail delay never touch real time.

Each test maps to one backlog task:

- T5.1  ``_flatten_checks`` -> flat list, order preserved.
- T5.2  ``_iter_purchase_pages`` stop conditions + page increment.
- T5.3  transient retries with backoff via the injected sleeper.
- T5.4  ``_refresh_session`` happy path + missing-refresh-token error.
- T5.5  refresh-on-auth exactly once under a lock; second auth re-raises.
- T5.6  ``_fetch_detail`` builds get_bill params; empty work_station_id -> fallback.
- T5.7  ``crawl_purchase_history`` stitching + order + counters.
- T5.8  one failing detail -> detail=None + CrawlItemError, the rest continue.
- T5.9  with_details / with_bonuses / max_pages flag gating.
- T5.10 request_delay sleep between detail fetches.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from novus_receipts.config import AppConfig
from novus_receipts.crawler.pagination import iter_purchase_pages
from novus_receipts.crawler.purchases_crawler import PurchasesCrawler
from novus_receipts.crawler.results import CrawlItemError, CrawlResult, ReceiptBundle
from novus_receipts.dto.auth import ConfirmWithOtpResponse
from novus_receipts.dto.bill import BillResponse, PurchaseDetalizationResponse
from novus_receipts.dto.bonuses import UserBonusResponse
from novus_receipts.dto.purchases import (
    Purchase2Response,
    PurchaseOperationsDetails,
    PurchaseResponse,
)
from novus_receipts.errors import (
    NovusApiError,
    NovusAuthError,
    NovusRateLimitError,
    NovusTransientError,
)

# --------------------------------------------------------------------------- #
# Builders                                                                     #
# --------------------------------------------------------------------------- #


def make_check(
    *,
    id_: str = "1718000000",
    shop_id: str = "123",
    check_number: str = "42",
    date: int = 1718000000,
    work_station_id: str = "9",
) -> PurchaseResponse:
    """Build one receipt-list item with the identity keys used downstream."""

    return PurchaseResponse.model_validate(
        {
            "raw_end_time_stamp": id_,
            "cash_id": 7,
            "shop_id": shop_id,
            "amount": "250.50",
            "bonus": "12.30",
            "check_number": check_number,
            "date": date,
            "shop_address": "Kyiv",
            "work_station_id": work_station_id,
        }
    )


def make_page(
    months: list[list[PurchaseResponse]],
    *,
    page: int = 1,
    total_count: int = 0,
    limit: int = 10,
) -> Purchase2Response:
    """Build a ``Purchase2Response`` from a list of month groups of checks."""

    groups = [
        PurchaseOperationsDetails(month=202401 + i, amount="0", data=checks)
        for i, checks in enumerate(months)
    ]
    return Purchase2Response(
        data=groups, limit=limit, page=page, total_count=total_count
    )


def make_bill(check_number: str = "42") -> BillResponse:
    """Minimal valid ``BillResponse`` for a detail fetch."""

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


def make_purchase_detail(check_number: str = "42") -> PurchaseDetalizationResponse:
    """Minimal valid ``PurchaseDetalizationResponse`` (the get_purchase fallback)."""

    return PurchaseDetalizationResponse.model_validate(
        {
            "id": 1,
            "amount": "250.50",
            "bonuses_accrued": "0",
            "bonuses_written_off": "0",
            "check_number": check_number,
            "coupons": [],
            "date": 1718000000,
            "goods": [],
            "payment_method": "card",
            "shop_address": "Kyiv",
            "shop_id": "123",
        }
    )


def make_refresh_response(token: str, refresh: str) -> ConfirmWithOtpResponse:
    """Build a refresh-token response carrying a fresh token + refresh token."""

    return ConfirmWithOtpResponse.model_validate(
        {
            "token": token,
            "refresh_token": refresh,
            "first_authorization": False,
            "user_first_name": "Test",
            "bonuses": "0",
            "bonus_type": "x",
            "blocked_card": False,
        }
    )


def make_bonuses() -> UserBonusResponse:
    """Minimal current-bonuses balance."""

    return UserBonusResponse.model_validate({"data": "12.30", "data_long": 1230})


def make_config(**overrides: Any) -> AppConfig:
    """Build an AppConfig with deterministic, fast-test defaults."""

    base: dict[str, Any] = {
        "user_token": "tok-0",
        "refresh_token": "refresh-0",
        "max_retries": 3,
        "backoff_base_s": 1.0,
        "request_delay_s": 0.2,
    }
    base.update(overrides)
    return AppConfig(**base)


# --------------------------------------------------------------------------- #
# Fake Api double                                                              #
# --------------------------------------------------------------------------- #


class FakeApi:
    """Scripted stand-in for ``NovusApiClient``.

    Each endpoint either replays queued return values or raises queued
    exceptions, recording every call so tests can assert order and arguments.
    """

    def __init__(self) -> None:
        self.access_token = "tok-0"
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

        # Queues of side effects (value or Exception) per method.
        self._purchases_2: list[Any] = []
        self._bill: list[Any] = []
        self._purchase: list[Any] = []
        self._bonuses: list[Any] = []
        self._refresh: list[Any] = []

    # -- scripting helpers --------------------------------------------------

    def queue_purchases_2(self, *items: Any) -> None:
        self._purchases_2.extend(items)

    def queue_bill(self, *items: Any) -> None:
        self._bill.extend(items)

    def queue_purchase(self, *items: Any) -> None:
        self._purchase.extend(items)

    def queue_bonuses(self, *items: Any) -> None:
        self._bonuses.extend(items)

    def queue_refresh(self, *items: Any) -> None:
        self._refresh.extend(items)

    @staticmethod
    def _next(queue: list[Any]) -> Any:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    # -- api surface --------------------------------------------------------

    def get_purchases_2(self, page: int = 1) -> Any:
        self.calls.append(("get_purchases_2", (page,)))
        return self._next(self._purchases_2)

    def get_bill(
        self, store: str, date: int, check_number: str, work_station_id: str
    ) -> Any:
        self.calls.append(("get_bill", (store, date, check_number, work_station_id)))
        return self._next(self._bill)

    def get_purchase(self, store: str, date: int, check_number: str) -> Any:
        self.calls.append(("get_purchase", (store, date, check_number)))
        return self._next(self._purchase)

    def get_current_bonuses(self) -> Any:
        self.calls.append(("get_current_bonuses", ()))
        return self._next(self._bonuses)

    def refresh_token(self, refresh_token: str) -> Any:
        self.calls.append(("refresh_token", (refresh_token,)))
        return self._next(self._refresh)

    def set_access_token(self, token: str) -> None:
        self.calls.append(("set_access_token", (token,)))
        self.access_token = token

    def method_calls(self, name: str) -> list[tuple[Any, ...]]:
        """All recorded argument tuples for a given method name, in order."""

        return [args for called, args in self.calls if called == name]


# NOVUS_* env vars AppConfig reads; cleared so the host env / a developer .env
# never leaks into the crawler tests (which build configs explicitly).
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
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _NOVUS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


def make_crawler(api: FakeApi, sleeper: Callable[[float], None], **cfg: Any) -> Any:
    """Build a crawler over the fake api with the injected sleeper."""

    return PurchasesCrawler(api, make_config(**cfg), sleeper=sleeper)


# --------------------------------------------------------------------------- #
# T5.1 _flatten_checks                                                         #
# --------------------------------------------------------------------------- #


def test_flatten_checks_preserves_order(api: FakeApi, fake_sleeper: Any) -> None:
    a, b, c, d = (make_check(check_number=str(n)) for n in range(4))
    page = make_page([[a, b], [c, d]], total_count=4)

    crawler = make_crawler(api, fake_sleeper)
    flat = crawler._flatten_checks(page)

    assert flat == [a, b, c, d]


def test_flatten_checks_empty_months(api: FakeApi, fake_sleeper: Any) -> None:
    crawler = make_crawler(api, fake_sleeper)
    assert crawler._flatten_checks(make_page([], total_count=0)) == []
    assert crawler._flatten_checks(make_page([[]], total_count=0)) == []


# --------------------------------------------------------------------------- #
# T5.2 _iter_purchase_pages                                                    #
# --------------------------------------------------------------------------- #


def test_iter_pages_stops_on_empty_page() -> None:
    seen_pages: list[int] = []

    def fetch(page: int) -> Purchase2Response:
        seen_pages.append(page)
        if page == 1:
            return make_page([[make_check()]], page=1, total_count=0)
        return make_page([], page=page, total_count=0)

    pages = list(iter_purchase_pages(fetch))

    # Page 1 yielded; page 2 was empty -> stop, not yielded.
    assert len(pages) == 1
    assert seen_pages == [1, 2]


def test_iter_pages_stops_when_seen_reaches_total_count() -> None:
    def fetch(page: int) -> Purchase2Response:
        check = make_check(check_number=f"p{page}")
        return make_page([[check]], page=page, total_count=2)

    pages = list(iter_purchase_pages(fetch))

    # total_count=2, one check per page -> two pages then stop (no page-3 fetch).
    assert [p.page for p in pages] == [1, 2]


def test_iter_pages_increments_page_1_2_3() -> None:
    requested: list[int] = []

    def fetch(page: int) -> Purchase2Response:
        requested.append(page)
        if page <= 3:
            return make_page([[make_check()]], page=page, total_count=99)
        return make_page([], page=page, total_count=99)

    list(iter_purchase_pages(fetch))

    assert requested == [1, 2, 3, 4]


# --------------------------------------------------------------------------- #
# T5.3 _call_with_resilience: transient retries + backoff                      #
# --------------------------------------------------------------------------- #


def test_resilience_retries_transient_then_succeeds(
    api: FakeApi, fake_sleeper: Any
) -> None:
    crawler = make_crawler(api, fake_sleeper, max_retries=3, backoff_base_s=1.0)
    attempts = {"n": 0}

    def fn() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise NovusTransientError("boom")
        return "ok"

    result = crawler._call_with_resilience(fn)

    assert result == "ok"
    assert attempts["n"] == 3
    # Two backoffs slept (between attempts 1->2 and 2->3), exponential growth.
    assert len(fake_sleeper.calls) == 2
    assert fake_sleeper.calls[0] < fake_sleeper.calls[1]


def test_resilience_exhausts_retries_and_reraises(
    api: FakeApi, fake_sleeper: Any
) -> None:
    crawler = make_crawler(api, fake_sleeper, max_retries=3)
    attempts = {"n": 0}

    def fn() -> str:
        attempts["n"] += 1
        raise NovusTransientError("always")

    with pytest.raises(NovusTransientError):
        crawler._call_with_resilience(fn)

    assert attempts["n"] == 3


def test_resilience_retries_rate_limit_using_retry_after(
    api: FakeApi, fake_sleeper: Any
) -> None:
    # PLAN.md §4.5 step 4: a 429 is retried, waiting for Retry-After when given.
    crawler = make_crawler(api, fake_sleeper, max_retries=3, backoff_base_s=1.0)
    attempts = {"n": 0}

    def fn() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise NovusRateLimitError("slow down", retry_after=7.0)
        return "ok"

    result = crawler._call_with_resilience(fn)

    assert result == "ok"
    assert attempts["n"] == 3
    # Both backoffs honoured the server-provided Retry-After of 7s.
    assert fake_sleeper.calls == [7.0, 7.0]


def test_resilience_rate_limit_without_retry_after_uses_backoff(
    api: FakeApi, fake_sleeper: Any
) -> None:
    # Without a Retry-After header it falls back to exponential backoff.
    crawler = make_crawler(api, fake_sleeper, max_retries=3, backoff_base_s=1.0)
    attempts = {"n": 0}

    def fn() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise NovusRateLimitError("slow down")
        return "ok"

    result = crawler._call_with_resilience(fn)

    assert result == "ok"
    assert len(fake_sleeper.calls) == 2
    assert fake_sleeper.calls[0] < fake_sleeper.calls[1]


def test_resilience_exhausts_rate_limit_retries_and_reraises(
    api: FakeApi, fake_sleeper: Any
) -> None:
    crawler = make_crawler(api, fake_sleeper, max_retries=3)
    attempts = {"n": 0}

    def fn() -> str:
        attempts["n"] += 1
        raise NovusRateLimitError("always", retry_after=1.0)

    with pytest.raises(NovusRateLimitError):
        crawler._call_with_resilience(fn)

    assert attempts["n"] == 3


def test_resilience_does_not_retry_fatal_api_error(
    api: FakeApi, fake_sleeper: Any
) -> None:
    crawler = make_crawler(api, fake_sleeper)
    attempts = {"n": 0}

    def fn() -> str:
        attempts["n"] += 1
        raise NovusApiError(status=400, body="bad")

    with pytest.raises(NovusApiError):
        crawler._call_with_resilience(fn)

    assert attempts["n"] == 1
    assert fake_sleeper.calls == []


# --------------------------------------------------------------------------- #
# T5.4 _refresh_session                                                        #
# --------------------------------------------------------------------------- #


def test_refresh_session_happy_path(api: FakeApi, fake_sleeper: Any) -> None:
    config = make_config(refresh_token="refresh-0")
    crawler = PurchasesCrawler(api, config, sleeper=fake_sleeper)
    api.queue_refresh(make_refresh_response(token="tok-1", refresh="refresh-1"))

    crawler._refresh_session()

    assert api.method_calls("refresh_token") == [("refresh-0",)]
    assert api.method_calls("set_access_token") == [("tok-1",)]
    assert config.refresh_token == "refresh-1"


def test_refresh_session_without_refresh_token_raises(
    api: FakeApi, fake_sleeper: Any
) -> None:
    config = make_config(refresh_token=None)
    crawler = PurchasesCrawler(api, config, sleeper=fake_sleeper)

    with pytest.raises(NovusAuthError):
        crawler._refresh_session()

    assert api.method_calls("refresh_token") == []


# --------------------------------------------------------------------------- #
# T5.5 _call_with_resilience: refresh-on-auth                                  #
# --------------------------------------------------------------------------- #


def test_resilience_refreshes_once_on_auth_then_retries(
    api: FakeApi, fake_sleeper: Any
) -> None:
    config = make_config(refresh_token="refresh-0")
    crawler = PurchasesCrawler(api, config, sleeper=fake_sleeper)
    api.queue_refresh(make_refresh_response(token="tok-1", refresh="refresh-1"))

    attempts = {"n": 0}

    def fn() -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise NovusAuthError("expired")
        return "ok"

    result = crawler._call_with_resilience(fn)

    assert result == "ok"
    assert attempts["n"] == 2
    assert api.method_calls("refresh_token") == [("refresh-0",)]


def test_resilience_second_auth_error_reraises(
    api: FakeApi, fake_sleeper: Any
) -> None:
    config = make_config(refresh_token="refresh-0")
    crawler = PurchasesCrawler(api, config, sleeper=fake_sleeper)
    api.queue_refresh(make_refresh_response(token="tok-1", refresh="refresh-1"))

    attempts = {"n": 0}

    def fn() -> str:
        attempts["n"] += 1
        raise NovusAuthError("still expired")

    with pytest.raises(NovusAuthError):
        crawler._call_with_resilience(fn)

    # fn called twice (original + one retry after refresh), refresh once.
    assert attempts["n"] == 2
    assert api.method_calls("refresh_token") == [("refresh-0",)]


def test_resilience_lock_prevents_double_refresh(
    api: FakeApi, fake_sleeper: Any
) -> None:
    """Concurrent auth failures must trigger exactly one refresh under the lock."""

    config = make_config(refresh_token="refresh-0")
    crawler = PurchasesCrawler(api, config, sleeper=fake_sleeper)

    refresh_count = {"n": 0}
    barrier = threading.Barrier(2)

    def slow_refresh(refresh_token: str) -> Any:
        # Serialised by the crawler's lock; count actual refresh executions.
        refresh_count["n"] += 1
        return make_refresh_response(token="tok-1", refresh="refresh-1")

    api.refresh_token = slow_refresh  # type: ignore[method-assign]

    def make_fn() -> Callable[[], str]:
        state = {"first": True}

        def fn() -> str:
            if state["first"]:
                state["first"] = False
                barrier.wait()
                raise NovusAuthError("expired")
            return "ok"

        return fn

    results: list[str] = []

    def worker() -> None:
        results.append(crawler._call_with_resilience(make_fn()))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == ["ok", "ok"]
    # The lock must serialise refresh so it executes exactly once total.
    assert refresh_count["n"] == 1


# --------------------------------------------------------------------------- #
# T5.6 _fetch_detail + fallback                                               #
# --------------------------------------------------------------------------- #


def test_fetch_detail_uses_get_bill_with_keys(api: FakeApi, fake_sleeper: Any) -> None:
    crawler = make_crawler(api, fake_sleeper)
    check = make_check(
        shop_id="123", date=1718000000, check_number="42", work_station_id="9"
    )
    api.queue_bill(make_bill())

    detail = crawler._fetch_detail(check)

    assert isinstance(detail, BillResponse)
    assert api.method_calls("get_bill") == [("123", 1718000000, "42", "9")]
    assert api.method_calls("get_purchase") == []


def test_fetch_detail_falls_back_when_work_station_empty(
    api: FakeApi, fake_sleeper: Any
) -> None:
    crawler = make_crawler(api, fake_sleeper)
    check = make_check(
        shop_id="123", date=1718000000, check_number="42", work_station_id=""
    )
    api.queue_purchase(make_purchase_detail())

    detail = crawler._fetch_detail(check)

    assert isinstance(detail, PurchaseDetalizationResponse)
    assert api.method_calls("get_bill") == []
    assert api.method_calls("get_purchase") == [("123", 1718000000, "42")]


# --------------------------------------------------------------------------- #
# T5.7 crawl_purchase_history: stitching + order + counters                    #
# --------------------------------------------------------------------------- #


def test_crawl_stitches_summary_and_detail_in_order(
    api: FakeApi, fake_sleeper: Any
) -> None:
    c1 = make_check(check_number="1", work_station_id="9")
    c2 = make_check(check_number="2", work_station_id="9")
    c3 = make_check(check_number="3", work_station_id="9")
    page = make_page([[c1, c2], [c3]], page=1, total_count=3)

    api.queue_purchases_2(page, make_page([], page=2, total_count=3))
    api.queue_bill(make_bill("1"), make_bill("2"), make_bill("3"))
    api.queue_bonuses(make_bonuses())

    crawler = make_crawler(api, fake_sleeper)
    result = crawler.crawl_purchase_history()

    assert isinstance(result, CrawlResult)
    assert [b.summary.check_number for b in result.receipts] == ["1", "2", "3"]
    assert all(isinstance(b, ReceiptBundle) for b in result.receipts)
    assert all(isinstance(b.detail, BillResponse) for b in result.receipts)
    assert result.pages_fetched == 1
    assert result.total_count == 3
    assert result.errors == []
    assert result.current_bonuses is not None


# --------------------------------------------------------------------------- #
# T5.8 non-fatal per-item errors                                              #
# --------------------------------------------------------------------------- #


def test_crawl_captures_detail_error_and_continues(
    api: FakeApi, fake_sleeper: Any
) -> None:
    c1 = make_check(check_number="1", work_station_id="9")
    c2 = make_check(check_number="2", work_station_id="9")
    page = make_page([[c1, c2]], page=1, total_count=2)

    api.queue_purchases_2(page, make_page([], page=2, total_count=2))
    # c1 detail blows up (fatal, non-retriable); c2 succeeds.
    api.queue_bill(NovusApiError(status=400, body="bad"), make_bill("2"))
    api.queue_bonuses(make_bonuses())

    crawler = make_crawler(api, fake_sleeper)
    result = crawler.crawl_purchase_history()

    assert [b.summary.check_number for b in result.receipts] == ["1", "2"]
    assert result.receipts[0].detail is None
    assert isinstance(result.receipts[1].detail, BillResponse)
    assert len(result.errors) == 1
    err = result.errors[0]
    assert isinstance(err, CrawlItemError)
    assert err.check.check_number == "1"
    assert isinstance(err.error, NovusApiError)


# --------------------------------------------------------------------------- #
# T5.9 flag gating: with_details / with_bonuses / max_pages                     #
# --------------------------------------------------------------------------- #


def test_with_details_false_skips_detail_fetches(
    api: FakeApi, fake_sleeper: Any
) -> None:
    page = make_page([[make_check()]], page=1, total_count=1)
    api.queue_purchases_2(page, make_page([], page=2, total_count=1))
    api.queue_bonuses(make_bonuses())

    crawler = make_crawler(api, fake_sleeper)
    result = crawler.crawl_purchase_history(with_details=False)

    assert api.method_calls("get_bill") == []
    assert api.method_calls("get_purchase") == []
    assert result.receipts[0].detail is None


def test_with_bonuses_false_skips_bonuses(api: FakeApi, fake_sleeper: Any) -> None:
    page = make_page([[make_check()]], page=1, total_count=1)
    api.queue_purchases_2(page, make_page([], page=2, total_count=1))
    api.queue_bill(make_bill())

    crawler = make_crawler(api, fake_sleeper)
    result = crawler.crawl_purchase_history(with_bonuses=False)

    assert api.method_calls("get_current_bonuses") == []
    assert result.current_bonuses is None


def test_with_bonuses_true_calls_get_current_bonuses(
    api: FakeApi, fake_sleeper: Any
) -> None:
    page = make_page([[make_check()]], page=1, total_count=1)
    api.queue_purchases_2(page, make_page([], page=2, total_count=1))
    api.queue_bill(make_bill())
    api.queue_bonuses(make_bonuses())

    crawler = make_crawler(api, fake_sleeper)
    result = crawler.crawl_purchase_history(with_bonuses=True)

    assert api.method_calls("get_current_bonuses") == [()]
    assert result.current_bonuses is not None


def test_max_pages_limits_pagination(api: FakeApi, fake_sleeper: Any) -> None:
    # Three full pages available, total_count large so pagination would continue.
    p1 = make_page([[make_check(check_number="1")]], page=1, total_count=99)
    p2 = make_page([[make_check(check_number="2")]], page=2, total_count=99)
    p3 = make_page([[make_check(check_number="3")]], page=3, total_count=99)
    api.queue_purchases_2(p1, p2, p3)
    api.queue_bill(make_bill("1"), make_bill("2"))
    api.queue_bonuses(make_bonuses())

    crawler = make_crawler(api, fake_sleeper)
    result = crawler.crawl_purchase_history(max_pages=2)

    assert result.pages_fetched == 2
    assert api.method_calls("get_purchases_2") == [(1,), (2,)]
    assert [b.summary.check_number for b in result.receipts] == ["1", "2"]


# --------------------------------------------------------------------------- #
# T5.10 request_delay between detail fetches                                   #
# --------------------------------------------------------------------------- #


def test_request_delay_slept_between_detail_fetches(
    api: FakeApi, fake_sleeper: Any
) -> None:
    c1 = make_check(check_number="1", work_station_id="9")
    c2 = make_check(check_number="2", work_station_id="9")
    page = make_page([[c1, c2]], page=1, total_count=2)
    api.queue_purchases_2(page, make_page([], page=2, total_count=2))
    api.queue_bill(make_bill("1"), make_bill("2"))
    api.queue_bonuses(make_bonuses())

    crawler = make_crawler(api, fake_sleeper, request_delay_s=0.2)
    crawler.crawl_purchase_history()

    # One polite delay per processed check, each equal to request_delay_s.
    assert fake_sleeper.calls == [0.2, 0.2]


def test_no_request_delay_when_details_disabled(
    api: FakeApi, fake_sleeper: Any
) -> None:
    page = make_page([[make_check()]], page=1, total_count=1)
    api.queue_purchases_2(page, make_page([], page=2, total_count=1))
    api.queue_bonuses(make_bonuses())

    crawler = make_crawler(api, fake_sleeper, request_delay_s=0.2)
    crawler.crawl_purchase_history(with_details=False)

    assert fake_sleeper.calls == []


# --------------------------------------------------------------------------- #
# pagination helper: reusable / injectable shape                               #
# --------------------------------------------------------------------------- #


def test_iter_purchase_pages_is_a_generator() -> None:
    def fetch(page: int) -> Purchase2Response:
        return make_page([], page=page, total_count=0)

    gen = iter_purchase_pages(fetch)
    assert isinstance(gen, Iterator)
    assert list(gen) == []
