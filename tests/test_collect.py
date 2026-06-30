"""``CollectJob`` orchestration + the ``collect`` CLI.

Mirrors ``test_entrypoint``'s mock-transport approach: a handler routes on path
and returns canned JSON, so no real network or time is used. Watermark logic is
checked with a spy job that records the ``from_`` it was called with.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from novus_receipts.collect import CollectJob, CollectStats
from novus_receipts.config import AppConfig
from novus_receipts.crawler.results import CrawlResult, ReceiptBundle
from novus_receipts.dto.purchases import PurchaseResponse
from novus_receipts.entrypoint import PurchaseHistoryJob
from novus_receipts.store.sqlite_store import ReceiptStore

# --------------------------------------------------------------------------- #
# Canned endpoint bodies + router                                              #
# --------------------------------------------------------------------------- #

_PROFILE_BODY = {"id": 1, "name": "Test User"}

_CHECK = {
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
_PAGE_1 = {
    "data": [{"month": 202401, "amount": "250.50", "data": [_CHECK]}],
    "limit": 10,
    "page": 1,
    "total_count": 1,
}
_PAGE_EMPTY = {"data": [], "limit": 10, "page": 2, "total_count": 1}
_BILL_BODY = {
    "id": 1,
    "amount": "250.50",
    "date": 1718000000,
    "check_number": "42",
    "shop_id": "123",
    "shop_address": "Київ",
    "payment_method": "card",
    "bonuses_accrued": "0",
    "bonuses_written_off": "0",
    "bonuses_details": [],
    "discounts_details": [],
    "total_discount_saving": "0",
    "total_promotion_saving": "0",
    "coupons": [],
    "is_csat_available": False,
    "goods": [
        {
            "title": "Молоко",
            "amount": "50.00",
            "quantity": "2",
            "price_type": "ШТ",
            "id": 555,
            "item_price": "25.00",
        }
    ],
}
_BONUSES_BODY = {"data": "12.30", "data_long": 1230}


class EndpointRouter:
    """Routes on path; ``detail_status`` lets a test fail the detail endpoint."""

    def __init__(self, *, detail_status: int = 200) -> None:
        self.detail_status = detail_status
        self._purchases_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/user/profile":
            return httpx.Response(200, json=_PROFILE_BODY)
        if path == "/user/purchases_2":
            self._purchases_calls += 1
            body = _PAGE_1 if self._purchases_calls == 1 else _PAGE_EMPTY
            return httpx.Response(200, json=body)
        if path == "/v2/user/purchase":
            if self.detail_status != 200:
                return httpx.Response(self.detail_status, json={})
            return httpx.Response(200, json=_BILL_BODY)
        if path == "/user/bonuses/current":
            return httpx.Response(200, json=_BONUSES_BODY)
        raise AssertionError(f"unexpected path: {path}")


def make_config(**overrides: object) -> AppConfig:
    fields: dict[str, object] = {
        "user_token": "start-token",
        "refresh_token": "refresh-0",
        "request_delay_s": 0.0,
    }
    fields.update(overrides)
    return AppConfig(**fields)  # type: ignore[arg-type]


def make_summary(*, check_number: str = "seed", date: int = 1_700_000_000) -> PurchaseResponse:
    return PurchaseResponse.model_validate(
        {
            "shop_id": "1",
            "amount": "1.00",
            "bonus": "0",
            "check_number": check_number,
            "date": date,
            "shop_address": "Kyiv",
        }
    )


def _count(store: ReceiptStore, table: str) -> int:
    return int(store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


class SpyJob:
    """Stand-in for PurchaseHistoryJob that records the ``from_`` it received."""

    def __init__(self) -> None:
        self.from_: datetime | None = None
        self.called = False

    def run(self, *, from_: datetime | None = None, mapper: Any = None) -> CrawlResult[Any]:
        self.called = True
        self.from_ = from_
        return CrawlResult(
            receipts=[], current_bonuses=None, pages_fetched=0, total_count=0, errors=[]
        )


# --------------------------------------------------------------------------- #
# Persisting + idempotency                                                     #
# --------------------------------------------------------------------------- #


def test_collect_persists_receipt(make_mock_client: Callable[..., httpx.Client]) -> None:
    config = make_config()
    client = make_mock_client(EndpointRouter(), base_url=config.base_url)
    job = PurchaseHistoryJob(config, http_client=client)
    store = ReceiptStore.open(":memory:", tz=UTC)
    try:
        stats = CollectJob(config, job=job, store=store).run()

        assert stats.receipts_upserted == 1
        assert stats.line_items_written == 1
        assert stats.products_touched == 1
        assert stats.errors_recorded == 0
        assert _count(store, "receipts") == 1
        assert _count(store, "receipt_items") == 1
        unit_price = store.connection.execute(
            "SELECT unit_price_cents FROM receipt_items"
        ).fetchone()[0]
        assert unit_price == 2500
    finally:
        store.close()


def test_collect_is_idempotent_across_runs(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    config = make_config()
    store = ReceiptStore.open(":memory:", tz=UTC)
    try:
        for _ in range(2):  # a fresh client each run (run() closes the client)
            client = make_mock_client(EndpointRouter(), base_url=config.base_url)
            job = PurchaseHistoryJob(config, http_client=client)
            CollectJob(config, job=job, store=store).run()

        assert _count(store, "receipts") == 1
        assert _count(store, "receipt_items") == 1
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Incremental cutoff resolution                                               #
# --------------------------------------------------------------------------- #


def test_watermark_seeds_from_minus_overlap() -> None:
    config = make_config(collect_overlap_s=100)
    store = ReceiptStore.open(":memory:", tz=UTC)
    try:
        store.upsert_bundle(
            ReceiptBundle(summary=make_summary(date=1_700_000_000), detail=None), now_ts=0
        )
        spy = SpyJob()
        CollectJob(config, job=spy, store=store).run()

        assert spy.from_ is not None
        assert spy.from_.timestamp() == 1_700_000_000 - 100
    finally:
        store.close()


def test_full_bypasses_watermark() -> None:
    config = make_config()
    store = ReceiptStore.open(":memory:", tz=UTC)
    try:
        store.upsert_bundle(
            ReceiptBundle(summary=make_summary(date=1_700_000_000), detail=None), now_ts=0
        )
        spy = SpyJob()
        CollectJob(config, job=spy, store=store).run(full=True)

        assert spy.called
        assert spy.from_ is None
    finally:
        store.close()


def test_explicit_from_overrides_watermark() -> None:
    config = make_config()
    store = ReceiptStore.open(":memory:", tz=UTC)
    try:
        store.upsert_bundle(
            ReceiptBundle(summary=make_summary(date=1_700_000_000), detail=None), now_ts=0
        )
        explicit = datetime(2020, 1, 1, tzinfo=UTC)
        spy = SpyJob()
        CollectJob(config, job=spy, store=store).run(from_=explicit)

        assert spy.from_ == explicit
    finally:
        store.close()


def test_empty_store_collects_full_history() -> None:
    config = make_config()
    store = ReceiptStore.open(":memory:", tz=UTC)
    try:
        spy = SpyJob()
        stats = CollectJob(config, job=spy, store=store).run()

        assert spy.called
        assert spy.from_ is None
        assert stats.watermark_before is None
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Detail failure: header stored + error recorded                               #
# --------------------------------------------------------------------------- #


def test_detail_failure_logs_to_sidecar_and_stores_header(
    make_mock_client: Callable[..., httpx.Client], tmp_path: Path
) -> None:
    db = tmp_path / "x.db"
    config = make_config(max_retries=1, db_path=str(db))
    client = make_mock_client(EndpointRouter(detail_status=500), base_url=config.base_url)
    job = PurchaseHistoryJob(config, http_client=client)
    store = ReceiptStore.open(":memory:", tz=UTC)
    try:
        stats = CollectJob(config, job=job, store=store).run()

        assert stats.receipts_upserted == 1
        assert stats.errors_recorded == 1
        assert _count(store, "receipts") == 1
        assert _count(store, "receipt_items") == 0
        detail_missing = store.connection.execute(
            "SELECT detail_missing FROM receipts"
        ).fetchone()[0]
        assert detail_missing == 1

        # the error went to the sidecar JSONL log, NOT the business DB
        log = tmp_path / "x.db.errors.jsonl"
        lines = log.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["check_number"] == "42"
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def test_main_returns_zero_and_prints_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from novus_receipts import collect as cli

    monkeypatch.setenv("NOVUS_USER_TOKEN", "tok")

    class FakeCollectJob:
        def __init__(self, config: AppConfig) -> None:
            self.config = config

        def run(self, *, from_: datetime | None = None, full: bool = False) -> CollectStats:
            return CollectStats(
                receipts_upserted=2,
                products_touched=3,
                line_items_written=5,
                errors_recorded=0,
                watermark_before=None,
                from_used=None,
            )

    monkeypatch.setattr(cli, "CollectJob", FakeCollectJob)

    rc = cli.main(argv=[])

    assert rc == 0
    out = capsys.readouterr().out
    assert "2 receipts" in out
    assert "5 line items" in out
    assert "3 products" in out
