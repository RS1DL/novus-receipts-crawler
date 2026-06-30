"""SQLite schema + migration for the receipt store (normalized, v2).

Money is stored as INTEGER minor units (``*_cents``); every time-bearing row
keeps the raw Unix ``date``/``ts`` (the watermark basis) and an ISO column
rendered in the configured timezone. The schema is normalized -- no descriptive
column is duplicated across tables:

- ``shops`` -- one row per store; ``address`` lives here, not on every receipt.
- ``receipts`` -- one row per receipt (header), keyed by a deterministic UUID
  (see :mod:`novus_receipts.store.ids`); references ``shops``.
- ``products`` -- the dynamic catalogue keyed by the stable ``GoodResponse.id``;
  the *only* home of ``title``/``price_type``.
- ``receipt_items`` -- the junction "which products appeared in which receipts",
  one row per goods line, carrying only the per-appearance facts (quantity,
  prices). No copied title/price_type.
- ``meta`` -- key/value bookkeeping.

Two views join the descriptive data back in: ``v_receipt_contents`` ("what was in
a receipt") and ``v_price_series`` ("a product's price over time"). Operational
crawl errors are *not* stored here -- they go to a sidecar JSONL log
(:mod:`novus_receipts.store.error_log`).
"""

from __future__ import annotations

import sqlite3

#: Bumped when the DDL below changes; mirrored into ``PRAGMA user_version``.
SCHEMA_VERSION = 2

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Reference table: one row per physical store. address lives here ONCE, keyed by
-- the natural shop_id, instead of being copied onto every receipt.
CREATE TABLE IF NOT EXISTS shops (
    shop_id       TEXT PRIMARY KEY,
    address       TEXT,
    first_seen_ts INTEGER NOT NULL,
    last_seen_ts  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS receipts (
    id              TEXT    PRIMARY KEY,      -- deterministic UUID of the natural key
    check_number    TEXT    NOT NULL,
    date            INTEGER NOT NULL,         -- raw Unix seconds (watermark basis)
    date_iso        TEXT    NOT NULL,         -- ISO-8601 in the configured tz
    shop_id         TEXT    NOT NULL REFERENCES shops(shop_id),
    receipt_uid     TEXT,                     -- summary.id (raw_end_time_stamp); nullable
    cash_id         INTEGER,
    work_station_id TEXT,
    amount_cents    INTEGER NOT NULL,
    bonus_cents     INTEGER,
    payment_method  TEXT,
    bonuses_accrued_cents        INTEGER,
    bonuses_written_off_cents    INTEGER,
    total_discount_saving_cents  INTEGER,
    total_promotion_saving_cents INTEGER,
    detail_source   TEXT    NOT NULL DEFAULT 'none',
    detail_missing  INTEGER NOT NULL DEFAULT 1,
    first_seen_ts   INTEGER NOT NULL,
    last_seen_ts    INTEGER NOT NULL,
    UNIQUE (check_number, date, shop_id)
);
CREATE INDEX IF NOT EXISTS ix_receipts_date ON receipts (date);
CREATE INDEX IF NOT EXISTS ix_receipts_shop ON receipts (shop_id);
-- Reserved for finding header-only receipts to re-fetch (a future --backfill pass).
CREATE INDEX IF NOT EXISTS ix_receipts_detail_missing
    ON receipts (detail_missing) WHERE detail_missing = 1;

CREATE TABLE IF NOT EXISTS products (
    id            INTEGER PRIMARY KEY,        -- == GoodResponse.id (stable, not a surrogate)
    title         TEXT NOT NULL,
    price_type    TEXT NOT NULL,
    image         TEXT,
    rating        TEXT,
    first_seen_ts INTEGER NOT NULL,
    last_seen_ts  INTEGER NOT NULL
);

-- The junction: which products appeared in which receipts, with the
-- per-appearance price observation. No copied title/price_type.
CREATE TABLE IF NOT EXISTS receipt_items (
    receipt_id   TEXT    NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    line_no      INTEGER NOT NULL,            -- 0-based index in detail.goods
    product_id   INTEGER NOT NULL REFERENCES products(id),
    quantity     TEXT    NOT NULL,            -- verbatim ("0.738", "2")
    quantity_num REAL,                         -- parsed for math (NULL if unparseable)
    amount_cents         INTEGER NOT NULL,     -- line total
    item_price_cents     INTEGER,              -- good.item_price (per-unit as given)
    old_price_cents      INTEGER,
    unit_price_cents     INTEGER,              -- DERIVED per-unit
    unit_price_source    TEXT    NOT NULL,     -- 'item_price' | 'amount_per_qty' | 'unknown'
    discount_total_cents INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (receipt_id, line_no)
);
CREATE INDEX IF NOT EXISTS ix_receipt_items_product ON receipt_items (product_id);

-- "What products were in which receipt" -- the human-readable join.
CREATE VIEW IF NOT EXISTS v_receipt_contents AS
SELECT r.id            AS receipt_id,
       r.check_number,
       r.date          AS ts,
       r.date_iso       AS ts_iso,
       r.shop_id,
       s.address       AS shop_address,
       ri.line_no,
       ri.product_id,
       p.title         AS product_title,
       p.price_type,
       ri.quantity,
       ri.quantity_num,
       ri.unit_price_cents,
       ri.unit_price_source,
       ri.amount_cents,
       ri.discount_total_cents
FROM receipt_items ri
JOIN receipts r ON r.id = ri.receipt_id
JOIN products p ON p.id = ri.product_id
LEFT JOIN shops s ON s.shop_id = r.shop_id
ORDER BY r.date, r.id, ri.line_no;

-- A product's price over time (descriptive data joined back from products/shops).
CREATE VIEW IF NOT EXISTS v_price_series AS
SELECT ri.product_id,
       p.title       AS title,
       p.price_type  AS price_type,
       r.date        AS ts,
       r.date_iso    AS ts_iso,
       r.shop_id,
       s.address     AS shop_address,
       ri.quantity,
       ri.quantity_num,
       ri.unit_price_cents,
       ri.unit_price_source,
       ri.item_price_cents,
       ri.old_price_cents,
       ri.amount_cents,
       ri.discount_total_cents,
       ri.receipt_id,
       ri.line_no
FROM receipt_items ri
JOIN receipts r ON r.id = ri.receipt_id
JOIN products p ON p.id = ri.product_id
LEFT JOIN shops s ON s.shop_id = r.shop_id;
"""

# Upgrading from an older version drops and recreates the business tables:
# receipt data is fully reproducible via ``collect --full``, so we don't
# ALTER-migrate rows. ``meta`` is preserved. Children are dropped before parents.
_DROP_V1_SQL = """
DROP TABLE IF EXISTS crawl_errors;
DROP TABLE IF EXISTS line_items;
DROP TABLE IF EXISTS receipt_items;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS receipts;
DROP TABLE IF EXISTS shops;
"""

# Views are always dropped before recreate so a future schema bump that edits a
# view body is never silently ignored by ``CREATE VIEW IF NOT EXISTS``.
_DROP_VIEWS_SQL = """
DROP VIEW IF EXISTS v_receipt_contents;
DROP VIEW IF EXISTS v_price_series;
"""


def migrate(conn: sqlite3.Connection) -> None:
    """Create or upgrade the schema; forward-only, idempotent on a current DB.

    A fresh database (``user_version = 0``) just gets the v2 schema. An older
    database is normalised by **dropping and recreating** the business tables --
    receipt data is fully reproducible via ``collect --full`` -- and ``meta`` is
    kept. After an upgrade the watermark is empty, so the next ``collect`` resolves
    to a full re-pull automatically. A v2 database is a no-op.

    The whole migration runs in one transaction (``BEGIN .. COMMIT``), so a crash
    mid-migration rolls back cleanly rather than leaving a half-applied schema.
    """

    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version >= SCHEMA_VERSION:
        return
    parts = ["BEGIN;"]
    if version >= 1:
        parts.append(_DROP_V1_SQL)
    parts.append(_DROP_VIEWS_SQL)
    parts.append(_SCHEMA_SQL)
    # PRAGMA cannot be parameterised; the value is a trusted int constant. It is
    # transactional, so it commits atomically with the DDL above.
    parts.append(f"PRAGMA user_version = {SCHEMA_VERSION};")
    parts.append("COMMIT;")
    conn.executescript("\n".join(parts))
