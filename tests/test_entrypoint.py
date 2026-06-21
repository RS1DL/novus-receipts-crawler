"""Tests for the entrypoint job + CLI (TASKS.md T7.1-T7.3, PLAN.md §5).

These drive :class:`PurchaseHistoryJob` and :func:`main` over a mocked
``httpx`` transport (via the ``make_mock_client`` fixture) so no real network or
time is used. The handler dispatches on the request path to return canned JSON
for each Novus endpoint the job touches.

- T7.1  ``run()`` returns a ``CrawlResult``; the ``httpx.Client`` is always
        closed, even when crawling raises.
- T7.2  the starting ``user_token`` from config is applied to the client, and
        ``get_profile`` runs before crawling unless the health check is disabled.
- T7.3  ``main()`` returns ``0`` and prints serialisable JSON to stdout.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from novus_receipts.__main__ import parse_from
from novus_receipts.config import AppConfig
from novus_receipts.crawler.results import CrawlResult
from novus_receipts.entrypoint import PurchaseHistoryJob
from novus_receipts.errors import NovusTransientError

# --------------------------------------------------------------------------- #
# Canned endpoint bodies                                                       #
# --------------------------------------------------------------------------- #

_PROFILE_BODY = {"id": 1, "name": "Test User"}

_PURCHASE_CHECK = {
    "raw_end_time_stamp": "1718000000",
    "cash_id": 7,
    "shop_id": "123",
    "amount": "250.50",
    "bonus": "12.30",
    "check_number": "42",
    "date": 1718000000,
    "shop_address": "Київ",
    "work_station_id": "9",
}

_PURCHASES_PAGE_1 = {
    "data": [{"month": 202401, "amount": "250.50", "data": [_PURCHASE_CHECK]}],
    "limit": 10,
    "page": 1,
    "total_count": 1,
}

_PURCHASES_PAGE_EMPTY = {"data": [], "limit": 10, "page": 2, "total_count": 1}

_BILL_BODY = {
    "id": 1,
    "amount": "250.50",
    "date": 1718000000,
    "check_number": "42",
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

_BONUSES_BODY = {"data": "12.30", "data_long": 1230}


class EndpointRouter:
    """A MockTransport handler routing on path; records every request path."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self._purchases_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        if path == "/user/profile":
            return httpx.Response(200, json=_PROFILE_BODY)
        if path == "/user/purchases_2":
            self._purchases_calls += 1
            body = _PURCHASES_PAGE_1 if self._purchases_calls == 1 else _PURCHASES_PAGE_EMPTY
            return httpx.Response(200, json=body)
        if path == "/v2/user/purchase":
            return httpx.Response(200, json=_BILL_BODY)
        if path == "/user/bonuses/current":
            return httpx.Response(200, json=_BONUSES_BODY)
        raise AssertionError(f"unexpected path: {path}")


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
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _NOVUS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def make_config(**overrides: object) -> AppConfig:
    fields: dict[str, object] = {
        "user_token": "start-token",
        "refresh_token": "refresh-0",
        "request_delay_s": 0.0,
    }
    fields.update(overrides)
    return AppConfig(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# T7.1 run() wiring + the client is always closed                              #
# --------------------------------------------------------------------------- #


def test_run_returns_crawl_result(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    config = make_config()
    client = make_mock_client(EndpointRouter(), base_url=config.base_url)
    job = PurchaseHistoryJob(config, http_client=client)

    result = job.run()

    assert isinstance(result, CrawlResult)
    assert [b.summary.check_number for b in result.receipts] == ["42"]
    assert result.current_bonuses is not None
    assert result.pages_fetched == 1
    # run() owns the client lifecycle and closes it on the happy path.
    assert client.is_closed


def test_run_closes_client_even_on_error(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    config = make_config(max_retries=1)

    def boom(request: httpx.Request) -> httpx.Response:
        # 500 on the first purchases page -> NovusTransientError after retries.
        return httpx.Response(500, json={})

    client = make_mock_client(boom, base_url=config.base_url)
    job = PurchaseHistoryJob(config, http_client=client, with_health_check=False)

    with pytest.raises(NovusTransientError):
        job.run()

    # The finally/with must close the client even though crawling raised.
    assert client.is_closed


# --------------------------------------------------------------------------- #
# T7.2 starting token + optional health check                                  #
# --------------------------------------------------------------------------- #


def test_run_applies_starting_user_token_to_requests(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    config = make_config(user_token="my-start-token")
    router = EndpointRouter()
    captured: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return router(request)

    client = make_mock_client(recording, base_url=config.base_url)
    PurchaseHistoryJob(config, http_client=client).run()

    # An authenticated request carries the user_token header from config.
    profile_reqs = [r for r in captured if r.url.path == "/user/profile"]
    assert profile_reqs
    assert profile_reqs[0].headers.get("user_token") == "my-start-token"


def test_health_check_runs_before_crawling_by_default(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    config = make_config()
    router = EndpointRouter()
    client = make_mock_client(router, base_url=config.base_url)

    PurchaseHistoryJob(config, http_client=client).run()

    # /user/profile is hit, and it precedes the first purchases page.
    assert "/user/profile" in router.paths
    assert router.paths.index("/user/profile") < router.paths.index("/user/purchases_2")


def test_health_check_can_be_disabled(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    config = make_config()
    router = EndpointRouter()
    client = make_mock_client(router, base_url=config.base_url)

    PurchaseHistoryJob(config, http_client=client, with_health_check=False).run()

    assert "/user/profile" not in router.paths


# --------------------------------------------------------------------------- #
# T7.3 main() CLI                                                              #
# --------------------------------------------------------------------------- #


def test_main_returns_zero_and_prints_serialisable_json(
    make_mock_client: Callable[..., httpx.Client],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from novus_receipts import __main__ as cli

    config = make_config()
    client = make_mock_client(EndpointRouter(), base_url=config.base_url)

    # Patch the job factory the CLI calls so it runs over the mock transport.
    def fake_job() -> PurchaseHistoryJob:
        return PurchaseHistoryJob(config, http_client=client)

    monkeypatch.setattr(cli, "PurchaseHistoryJob", fake_job)

    rc = cli.main(argv=[])

    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)  # stdout is valid JSON
    assert "receipts" in parsed
    assert parsed["pages_fetched"] == 1
    assert parsed["receipts"][0]["summary"]["check_number"] == "42"
    # Non-ASCII is written human-readable (ensure_ascii=False), not escaped.
    assert "Київ" in out
    assert "\\u04" not in out
    assert parsed["receipts"][0]["summary"]["shop_address"] == "Київ"
    # ReceiptMapper renders dates as human-readable ISO-8601 in the configured
    # timezone (default Europe/Kyiv -> +03:00 in June), not raw Unix seconds.
    assert parsed["receipts"][0]["summary"]["date"] == "2024-06-10T09:13:20+03:00"
    assert parsed["receipts"][0]["detail"]["date"] == "2024-06-10T09:13:20+03:00"
    # Money fields are numbers (not strings); the bonus balance too.
    assert parsed["receipts"][0]["summary"]["amount"] == 250.5
    assert isinstance(parsed["receipts"][0]["summary"]["amount"], float)
    assert parsed["current_bonuses"]["data"] == 12.3


# --- parse_from (CLI --from) ------------------------------------------------


def test_parse_from_duration_subtracts_from_now() -> None:
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    assert parse_from("7d", now=now, tz=UTC) == now - timedelta(days=7)
    assert parse_from("2w", now=now, tz=UTC) == now - timedelta(weeks=2)
    assert parse_from("24h", now=now, tz=UTC) == now - timedelta(hours=24)
    assert parse_from("30m", now=now, tz=UTC) == now - timedelta(minutes=30)


def test_parse_from_absolute_date_uses_configured_tz() -> None:
    tz = timezone(timedelta(hours=3))
    now = datetime(2026, 6, 17, tzinfo=tz)
    assert parse_from("2026-06-10", now=now, tz=tz) == datetime(2026, 6, 10, tzinfo=tz)


def test_parse_from_absolute_datetime_keeps_its_offset() -> None:
    now = datetime(2026, 6, 17, tzinfo=UTC)
    parsed = parse_from("2026-06-10T08:30:00+00:00", now=now, tz=UTC)
    assert parsed == datetime(2026, 6, 10, 8, 30, tzinfo=UTC)
