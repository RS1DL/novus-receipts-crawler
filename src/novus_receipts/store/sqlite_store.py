"""``ReceiptStore`` -- the SQLite persistence layer.

Consumes raw :class:`~novus_receipts.crawler.results.ReceiptBundle` DTOs (it owns
its own money/date normalisation rather than routing through ``ReceiptMapper``,
which would lose the typed contract and re-parse floats). Every public write is
an idempotent upsert keyed on stable keys, so re-collecting the same receipt --
e.g. via the incremental overlap window -- never duplicates rows.

The schema is normalized: descriptive data has a single home (``shops.address``,
``products.title``/``price_type``); ``receipt_items`` is the junction holding only
per-appearance facts. One receipt is written in a single transaction
(shop -> header -> products -> items), so a crash mid-crawl never leaves a
half-written receipt. Operational crawl errors are *not* stored here -- they go to
a sidecar log (:mod:`novus_receipts.store.error_log`).
"""

from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from datetime import tzinfo
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from novus_receipts.dto.bill import BillResponse, GoodResponse
from novus_receipts.mapping.mappers import format_timestamp
from novus_receipts.store.ids import receipt_guid
from novus_receipts.store.money import parse_cents, parse_qty
from novus_receipts.store.schema import migrate

if TYPE_CHECKING:
    import os
    from types import TracebackType

    from novus_receipts.crawler.results import ReceiptBundle

_SHOP_UPSERT = """
INSERT INTO shops (shop_id, address, first_seen_ts, last_seen_ts)
VALUES (:shop_id, :address, :now_ts, :now_ts)
ON CONFLICT(shop_id) DO UPDATE SET
    address = COALESCE(excluded.address, shops.address),
    last_seen_ts = excluded.last_seen_ts
"""

_RECEIPT_UPSERT = """
INSERT INTO receipts (
    id, check_number, date, date_iso, shop_id, receipt_uid,
    cash_id, work_station_id, amount_cents, bonus_cents, payment_method,
    bonuses_accrued_cents, bonuses_written_off_cents,
    total_discount_saving_cents, total_promotion_saving_cents,
    detail_source, detail_missing, first_seen_ts, last_seen_ts
) VALUES (
    :id, :check_number, :date, :date_iso, :shop_id, :receipt_uid,
    :cash_id, :work_station_id, :amount_cents, :bonus_cents, :payment_method,
    :bonuses_accrued_cents, :bonuses_written_off_cents,
    :total_discount_saving_cents, :total_promotion_saving_cents,
    :detail_source, :detail_missing, :now_ts, :now_ts
)
ON CONFLICT(check_number, date, shop_id) DO UPDATE SET
    date_iso = excluded.date_iso,
    receipt_uid = COALESCE(excluded.receipt_uid, receipts.receipt_uid),
    cash_id = COALESCE(excluded.cash_id, receipts.cash_id),
    work_station_id = COALESCE(excluded.work_station_id, receipts.work_station_id),
    amount_cents = excluded.amount_cents,
    bonus_cents = COALESCE(excluded.bonus_cents, receipts.bonus_cents),
    last_seen_ts = excluded.last_seen_ts,
    -- Detail fields only ever upgrade: a later header-only sighting must not
    -- clobber a detail we already stored.
    detail_missing = CASE WHEN excluded.detail_missing = 0
        THEN 0 ELSE receipts.detail_missing END,
    detail_source = CASE WHEN excluded.detail_missing = 0
        THEN excluded.detail_source ELSE receipts.detail_source END,
    payment_method = CASE WHEN excluded.detail_missing = 0
        THEN excluded.payment_method ELSE receipts.payment_method END,
    bonuses_accrued_cents = CASE WHEN excluded.detail_missing = 0
        THEN excluded.bonuses_accrued_cents ELSE receipts.bonuses_accrued_cents END,
    bonuses_written_off_cents = CASE WHEN excluded.detail_missing = 0
        THEN excluded.bonuses_written_off_cents ELSE receipts.bonuses_written_off_cents END,
    total_discount_saving_cents = CASE WHEN excluded.detail_missing = 0
        THEN excluded.total_discount_saving_cents ELSE receipts.total_discount_saving_cents END,
    total_promotion_saving_cents = CASE WHEN excluded.detail_missing = 0
        THEN excluded.total_promotion_saving_cents ELSE receipts.total_promotion_saving_cents END
"""

_PRODUCT_UPSERT = """
INSERT INTO products (id, title, price_type, image, rating, first_seen_ts, last_seen_ts)
VALUES (:id, :title, :price_type, :image, :rating, :now_ts, :now_ts)
ON CONFLICT(id) DO UPDATE SET
    title = excluded.title,
    price_type = excluded.price_type,
    image = COALESCE(excluded.image, products.image),
    rating = COALESCE(excluded.rating, products.rating),
    last_seen_ts = excluded.last_seen_ts
"""

_RECEIPT_ITEM_INSERT = """
INSERT INTO receipt_items (
    receipt_id, line_no, product_id, quantity, quantity_num,
    amount_cents, item_price_cents, old_price_cents, unit_price_cents,
    unit_price_source, discount_total_cents
) VALUES (
    :receipt_id, :line_no, :product_id, :quantity, :quantity_num,
    :amount_cents, :item_price_cents, :old_price_cents, :unit_price_cents,
    :unit_price_source, :discount_total_cents
)
"""


class ReceiptStore(AbstractContextManager["ReceiptStore"]):
    """A SQLite-backed store for collected receipts, products and price history."""

    def __init__(self, conn: sqlite3.Connection, *, tz: tzinfo) -> None:
        self._conn = conn
        self._tz = tz

    @classmethod
    def open(cls, db_path: str | os.PathLike[str], *, tz: tzinfo) -> ReceiptStore:
        """Open (creating if needed) the database, set pragmas, migrate, return it.

        WAL mode lets an ad-hoc reader query while ``collect`` writes;
        ``foreign_keys`` is enabled per-connection (off by default in SQLite).
        """

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        migrate(conn)
        return cls(conn, tz=tz)

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying connection (for read-only queries / later analysis)."""

        return self._conn

    def close(self) -> None:
        """Close the underlying connection."""

        self._conn.close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- reads --------------------------------------------------------------

    def get_watermark(self) -> int | None:
        """Largest receipt ``date`` (Unix seconds) stored, or ``None`` if empty."""

        row = self._conn.execute("SELECT MAX(date) FROM receipts").fetchone()
        value = row[0]
        if value is None:
            return None
        return int(value)

    # -- writes -------------------------------------------------------------

    def upsert_bundle(self, bundle: ReceiptBundle, *, now_ts: int) -> str:
        """Upsert one receipt (shop + header + products + items) in one transaction.

        Header-only when ``bundle.detail`` is ``None`` (``detail_missing = 1``); a
        later run *within the overlap window* that obtains the detail backfills the
        items (older header-only receipts would need an explicit backfill pass over
        ``ix_receipts_detail_missing``). Returns the receipt's deterministic UUID id.
        """

        summary = bundle.summary
        detail = bundle.detail

        detail_missing = 0 if detail is not None else 1
        detail_source = "none"
        payment_method: str | None = None
        bonuses_accrued_cents: int | None = None
        bonuses_written_off_cents: int | None = None
        total_discount_saving_cents: int | None = None
        total_promotion_saving_cents: int | None = None
        if detail is not None:
            detail_source = "bill" if isinstance(detail, BillResponse) else "detalization"
            payment_method = detail.payment_method
            bonuses_accrued_cents = parse_cents(detail.bonuses_accrued)
            bonuses_written_off_cents = parse_cents(detail.bonuses_written_off)
            total_discount_saving_cents = parse_cents(detail.total_discount_saving)
            total_promotion_saving_cents = parse_cents(detail.total_promotion_saving)

        receipt_id = receipt_guid(summary.check_number, summary.date, summary.shop_id)
        header = {
            "id": receipt_id,
            "check_number": summary.check_number,
            "date": summary.date,
            "date_iso": format_timestamp(summary.date, self._tz),
            "shop_id": summary.shop_id,
            "receipt_uid": summary.id,
            "cash_id": summary.cash_id,
            "work_station_id": summary.work_station_id,
            "amount_cents": parse_cents(summary.amount) or 0,
            "bonus_cents": parse_cents(summary.bonus),
            "payment_method": payment_method,
            "bonuses_accrued_cents": bonuses_accrued_cents,
            "bonuses_written_off_cents": bonuses_written_off_cents,
            "total_discount_saving_cents": total_discount_saving_cents,
            "total_promotion_saving_cents": total_promotion_saving_cents,
            "detail_source": detail_source,
            "detail_missing": detail_missing,
            "now_ts": now_ts,
        }

        conn = self._conn
        with conn:
            # Shop before receipt: the receipts.shop_id FK requires the shop row.
            conn.execute(
                _SHOP_UPSERT,
                {"shop_id": summary.shop_id, "address": summary.shop_address, "now_ts": now_ts},
            )
            conn.execute(_RECEIPT_UPSERT, header)

            if detail is not None:
                # Re-fetched detail fully replaces this receipt's items: idempotent,
                # and drops any stale row should the goods list ever shrink.
                conn.execute("DELETE FROM receipt_items WHERE receipt_id = ?", (receipt_id,))
                for line_no, good in enumerate(detail.goods):
                    self._upsert_product(conn, good, now_ts)
                    conn.execute(
                        _RECEIPT_ITEM_INSERT, self._line_params(receipt_id, line_no, good)
                    )

        return receipt_id

    def set_last_collect_ts(self, ts: int) -> None:
        """Record the timestamp of the last successful collection (bookkeeping)."""

        with self._conn:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('last_collect_ts', :v)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                {"v": str(ts)},
            )

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _upsert_product(conn: sqlite3.Connection, good: GoodResponse, now_ts: int) -> None:
        rating = None if good.rating is None else str(good.rating)
        conn.execute(
            _PRODUCT_UPSERT,
            {
                "id": good.id,
                "title": good.title,
                "price_type": good.price_type,
                "image": good.image,
                "rating": rating,
                "now_ts": now_ts,
            },
        )

    @staticmethod
    def _line_params(receipt_id: str, line_no: int, good: GoodResponse) -> dict[str, Any]:
        quantity_num = parse_qty(good.quantity)
        amount_cents = parse_cents(good.amount) or 0
        item_price_cents = parse_cents(good.item_price)
        old_price_cents = parse_cents(good.old_price)

        unit_price_cents: int | None
        # ``item_price`` is the per-unit price for piece goods; for weighed goods
        # the API sends 0, so fall back to amount / quantity in that case.
        if item_price_cents:
            unit_price_cents = item_price_cents
            unit_price_source = "item_price"
        elif quantity_num is not None and quantity_num > 0:
            # Decimal half-up (matching parse_cents), not float round()'s half-even.
            unit_price_cents = int(
                (Decimal(amount_cents) / Decimal(str(quantity_num))).to_integral_value(
                    rounding=ROUND_HALF_UP
                )
            )
            unit_price_source = "amount_per_qty"
        else:
            unit_price_cents = None
            unit_price_source = "unknown"

        discount_total_cents = sum(
            (parse_cents(d.discount_amount) or 0) for d in good.discounts
        )

        return {
            "receipt_id": receipt_id,
            "line_no": line_no,
            "product_id": good.id,
            "quantity": good.quantity,
            "quantity_num": quantity_num,
            "amount_cents": amount_cents,
            "item_price_cents": item_price_cents,
            "old_price_cents": old_price_cents,
            "unit_price_cents": unit_price_cents,
            "unit_price_source": unit_price_source,
            "discount_total_cents": discount_total_cents,
        }
