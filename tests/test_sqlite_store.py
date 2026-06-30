"""``ReceiptStore`` upsert behaviour (the persistence core), normalized v2.

Bundles are built straight from DTOs. Everything runs against an in-memory
SQLite database; no real time is used (``now_ts`` is passed in).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC
from typing import Any

import pytest

from novus_receipts.crawler.results import ReceiptBundle
from novus_receipts.dto.bill import (
    BillResponse,
    GoodDiscount,
    GoodResponse,
    PurchaseDetalizationResponse,
)
from novus_receipts.dto.purchases import PurchaseResponse
from novus_receipts.store.ids import receipt_guid
from novus_receipts.store.sqlite_store import ReceiptStore

# --------------------------------------------------------------------------- #
# Builders                                                                     #
# --------------------------------------------------------------------------- #


def make_summary(
    *,
    check_number: str = "42",
    date: int = 1_718_000_000,
    shop_id: str = "123",
    shop_address: str = "Kyiv",
    amount: str = "250.50",
    bonus: str = "12.30",
    work_station_id: str | None = "9",
) -> PurchaseResponse:
    return PurchaseResponse.model_validate(
        {
            "raw_end_time_stamp": str(date),
            "cash_id": 7,
            "shop_id": shop_id,
            "amount": amount,
            "bonus": bonus,
            "check_number": check_number,
            "date": date,
            "shop_address": shop_address,
            "work_station_id": work_station_id,
        }
    )


def make_good(
    *,
    id: int = 10231,
    title: str = "Молоко",
    amount: str = "73.70",
    quantity: str = "0.224",
    price_type: str = "КГ",
    item_price: str | None = None,
    old_price: str | None = None,
    discounts: list[GoodDiscount] | None = None,
) -> GoodResponse:
    data: dict[str, Any] = {
        "title": title,
        "amount": amount,
        "quantity": quantity,
        "price_type": price_type,
        "id": id,
    }
    if item_price is not None:
        data["item_price"] = item_price
    if old_price is not None:
        data["old_price"] = old_price
    if discounts is not None:
        data["discounts"] = discounts
    return GoodResponse.model_validate(data)


def make_bill(
    summary: PurchaseResponse, *, goods: list[GoodResponse], payment_method: str = "card"
) -> BillResponse:
    return BillResponse.model_validate(
        {
            "id": 1,
            "amount": summary.amount,
            "date": summary.date,
            "check_number": summary.check_number,
            "shop_id": summary.shop_id,
            "shop_address": summary.shop_address,
            "payment_method": payment_method,
            "bonuses_accrued": "2.30",
            "bonuses_written_off": "0",
            "bonuses_details": [],
            "discounts_details": [],
            "total_discount_saving": "0",
            "total_promotion_saving": "0",
            "coupons": [],
            "is_csat_available": False,
            "goods": goods,
        }
    )


@pytest.fixture
def store() -> Iterator[ReceiptStore]:
    s = ReceiptStore.open(":memory:", tz=UTC)
    try:
        yield s
    finally:
        s.close()


def _count(store: ReceiptStore, table: str) -> int:
    return int(store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _upsert_one_good(store: ReceiptStore, good: GoodResponse) -> None:
    """Upsert a single-line receipt built around ``good`` (one per test)."""

    summary = make_summary()
    store.upsert_bundle(
        ReceiptBundle(summary=summary, detail=make_bill(summary, goods=[good])),
        now_ts=0,
    )


# --------------------------------------------------------------------------- #
# Idempotency + the normalized shape                                           #
# --------------------------------------------------------------------------- #


def test_upsert_is_idempotent(store: ReceiptStore) -> None:
    summary = make_summary()
    bundle = ReceiptBundle(
        summary=summary,
        detail=make_bill(summary, goods=[make_good(id=1), make_good(id=2)]),
    )

    store.upsert_bundle(bundle, now_ts=1000)
    store.upsert_bundle(bundle, now_ts=2000)  # re-collect: no duplicates

    assert _count(store, "shops") == 1
    assert _count(store, "receipts") == 1
    assert _count(store, "products") == 2
    assert _count(store, "receipt_items") == 2


def test_receipt_id_is_deterministic_guid(store: ReceiptStore) -> None:
    summary = make_summary()
    rid = store.upsert_bundle(ReceiptBundle(summary=summary, detail=None), now_ts=0)

    assert rid == receipt_guid(summary.check_number, summary.date, summary.shop_id)
    stored = store.connection.execute("SELECT id FROM receipts").fetchone()[0]
    assert stored == rid


def test_shop_stored_once_and_referenced(store: ReceiptStore) -> None:
    summary = make_summary(shop_id="7016", shop_address="м. Київ")
    store.upsert_bundle(ReceiptBundle(summary=summary, detail=None), now_ts=0)

    row = store.connection.execute("SELECT shop_id, address FROM shops").fetchone()
    assert row == ("7016", "м. Київ")
    # the receipt references the shop; address is NOT on the receipt row
    fk = store.connection.execute("SELECT shop_id FROM receipts").fetchone()[0]
    assert fk == "7016"


def test_watermark_empty_then_max(store: ReceiptStore) -> None:
    assert store.get_watermark() is None

    store.upsert_bundle(
        ReceiptBundle(summary=make_summary(check_number="a", date=100), detail=None), now_ts=0
    )
    store.upsert_bundle(
        ReceiptBundle(summary=make_summary(check_number="b", date=200), detail=None), now_ts=0
    )

    assert store.get_watermark() == 200


# --------------------------------------------------------------------------- #
# detail=None: header-only, backfill, no clobber                              #
# --------------------------------------------------------------------------- #


def test_detail_none_stores_header_only(store: ReceiptStore) -> None:
    summary = make_summary()
    store.upsert_bundle(ReceiptBundle(summary=summary, detail=None), now_ts=0)

    assert _count(store, "receipts") == 1
    assert _count(store, "receipt_items") == 0
    row = store.connection.execute(
        "SELECT detail_missing, detail_source FROM receipts"
    ).fetchone()
    assert row == (1, "none")


def test_detail_backfills_then_is_not_clobbered(store: ReceiptStore) -> None:
    summary = make_summary()

    store.upsert_bundle(ReceiptBundle(summary=summary, detail=None), now_ts=0)
    store.upsert_bundle(
        ReceiptBundle(summary=summary, detail=make_bill(summary, goods=[make_good(id=1)])),
        now_ts=1,
    )

    row = store.connection.execute(
        "SELECT detail_missing, payment_method FROM receipts"
    ).fetchone()
    assert row == (0, "card")
    assert _count(store, "receipt_items") == 1

    # a later header-only sighting must NOT wipe the stored detail
    store.upsert_bundle(ReceiptBundle(summary=summary, detail=None), now_ts=2)

    row = store.connection.execute(
        "SELECT detail_missing, payment_method FROM receipts"
    ).fetchone()
    assert row == (0, "card")
    assert _count(store, "receipt_items") == 1


# --------------------------------------------------------------------------- #
# Junction: same product twice in one receipt -> two items, one product        #
# --------------------------------------------------------------------------- #


def test_same_product_twice_in_one_receipt(store: ReceiptStore) -> None:
    summary = make_summary()
    bundle = ReceiptBundle(
        summary=summary,
        detail=make_bill(summary, goods=[make_good(id=77), make_good(id=77)]),
    )

    store.upsert_bundle(bundle, now_ts=0)

    assert _count(store, "products") == 1
    assert _count(store, "receipt_items") == 2
    line_nos = [
        r[0]
        for r in store.connection.execute(
            "SELECT line_no FROM receipt_items ORDER BY line_no"
        ).fetchall()
    ]
    assert line_nos == [0, 1]


# --------------------------------------------------------------------------- #
# Per-unit price derivation + provenance                                      #
# --------------------------------------------------------------------------- #


def _unit_price(store: ReceiptStore) -> tuple[Any, Any]:
    return store.connection.execute(
        "SELECT unit_price_cents, unit_price_source FROM receipt_items"
    ).fetchone()


def test_unit_price_from_item_price(store: ReceiptStore) -> None:
    _upsert_one_good(store, make_good(amount="50.00", quantity="2", item_price="25.00"))

    assert _unit_price(store) == (2500, "item_price")


def test_unit_price_from_amount_per_qty(store: ReceiptStore) -> None:
    _upsert_one_good(store, make_good(amount="10.00", quantity="2"))  # no item_price

    assert _unit_price(store) == (500, "amount_per_qty")


def test_unit_price_falls_back_when_item_price_zero(store: ReceiptStore) -> None:
    # Weighed goods carry item_price "0"; the real per-unit price is amount/qty.
    _upsert_one_good(store, make_good(amount="73.70", quantity="0.224", item_price="0.00"))

    assert _unit_price(store) == (32902, "amount_per_qty")


def test_unit_price_unknown_when_qty_zero(store: ReceiptStore) -> None:
    _upsert_one_good(store, make_good(amount="10.00", quantity="0"))  # no item_price, qty unusable

    assert _unit_price(store) == (None, "unknown")


def test_price_series_view_exposes_provenance(store: ReceiptStore) -> None:
    _upsert_one_good(store, make_good(amount="10.00", quantity="2"))  # amount_per_qty

    row = store.connection.execute(
        "SELECT unit_price_cents, unit_price_source, price_type, title FROM v_price_series"
    ).fetchone()
    assert row == (500, "amount_per_qty", "КГ", "Молоко")


def test_discount_total_summed_into_cents(store: ReceiptStore) -> None:
    _upsert_one_good(
        store,
        make_good(
            discounts=[
                GoodDiscount(discount_amount="1.50", discount_title="x", discount_type_title="t"),
                GoodDiscount(discount_amount="0.50", discount_title="y", discount_type_title="t"),
            ]
        ),
    )

    total = store.connection.execute(
        "SELECT discount_total_cents FROM receipt_items"
    ).fetchone()[0]
    assert total == 200


# --------------------------------------------------------------------------- #
# products holds the latest descriptive values (single home)                   #
# --------------------------------------------------------------------------- #


def test_product_price_type_reflects_latest(store: ReceiptStore) -> None:
    s1 = make_summary(check_number="r1", date=100)
    s2 = make_summary(check_number="r2", date=200)
    store.upsert_bundle(
        ReceiptBundle(summary=s1, detail=make_bill(s1, goods=[make_good(id=5, price_type="КГ")])),
        now_ts=0,
    )
    store.upsert_bundle(
        ReceiptBundle(summary=s2, detail=make_bill(s2, goods=[make_good(id=5, price_type="ШТ")])),
        now_ts=0,
    )

    assert _count(store, "products") == 1
    latest = store.connection.execute(
        "SELECT price_type FROM products WHERE id = 5"
    ).fetchone()[0]
    assert latest == "ШТ"


# --------------------------------------------------------------------------- #
# Receipt-contents view: which products in which receipt                       #
# --------------------------------------------------------------------------- #


def test_receipt_contents_view_joins_product_and_shop(store: ReceiptStore) -> None:
    summary = make_summary(shop_address="м. Київ")
    store.upsert_bundle(
        ReceiptBundle(summary=summary, detail=make_bill(summary, goods=[make_good(id=9)])),
        now_ts=0,
    )

    row = store.connection.execute(
        "SELECT check_number, product_title, price_type, shop_address FROM v_receipt_contents"
    ).fetchone()
    assert row == ("42", "Молоко", "КГ", "м. Київ")


# --------------------------------------------------------------------------- #
# Money as cents; detail variant; FK cascade                                   #
# --------------------------------------------------------------------------- #


def test_money_stored_as_cents(store: ReceiptStore) -> None:
    summary = make_summary(amount="232.45", bonus="2.32")
    store.upsert_bundle(ReceiptBundle(summary=summary, detail=None), now_ts=0)

    row = store.connection.execute("SELECT amount_cents, bonus_cents FROM receipts").fetchone()
    assert row == (23245, 232)


def test_non_finite_amount_does_not_drop_receipt(store: ReceiptStore) -> None:
    # A malformed money field from the API must not abort the whole receipt.
    store.upsert_bundle(
        ReceiptBundle(summary=make_summary(amount="Infinity"), detail=None), now_ts=0
    )

    assert _count(store, "receipts") == 1
    amount_cents = store.connection.execute("SELECT amount_cents FROM receipts").fetchone()[0]
    assert amount_cents == 0


def test_detalization_detail_source(store: ReceiptStore) -> None:
    summary = make_summary()
    detail = PurchaseDetalizationResponse.model_validate(
        {
            "amount": summary.amount,
            "bonuses_accrued": "0",
            "bonuses_written_off": "0",
            "check_number": summary.check_number,
            "date": summary.date,
            "payment_method": "Готівка",
            "shop_address": summary.shop_address,
            "shop_id": summary.shop_id,
            "goods": [make_good(id=9)],
        }
    )
    store.upsert_bundle(ReceiptBundle(summary=summary, detail=detail), now_ts=0)

    source = store.connection.execute("SELECT detail_source FROM receipts").fetchone()[0]
    assert source == "detalization"


def test_set_last_collect_ts(store: ReceiptStore) -> None:
    store.set_last_collect_ts(1234)
    value = store.connection.execute(
        "SELECT value FROM meta WHERE key = 'last_collect_ts'"
    ).fetchone()[0]
    assert value == "1234"


def test_delete_receipt_cascades_to_items(store: ReceiptStore) -> None:
    summary = make_summary()
    rid = store.upsert_bundle(
        ReceiptBundle(summary=summary, detail=make_bill(summary, goods=[make_good(id=1)])),
        now_ts=0,
    )
    assert _count(store, "receipt_items") == 1

    with store.connection:
        store.connection.execute("DELETE FROM receipts WHERE id = ?", (rid,))

    assert _count(store, "receipt_items") == 0
