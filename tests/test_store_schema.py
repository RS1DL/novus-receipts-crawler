"""Schema creation + migration (``store.schema``), normalized v2.

``migrate`` creates the tables/views and stamps ``PRAGMA user_version``; it is
idempotent and upgrades an old v1 database by dropping and recreating the
business tables (data is reproducible) while preserving ``meta``.
"""

from __future__ import annotations

import sqlite3

import pytest

from novus_receipts.store.schema import SCHEMA_VERSION, migrate

_EXPECTED_TABLES = {"meta", "shops", "receipts", "products", "receipt_items"}


def _object_names(conn: sqlite3.Connection, kind: str) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = ?", (kind,)
    ).fetchall()
    return {r[0] for r in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_migrate_creates_tables_views_and_version() -> None:
    conn = sqlite3.connect(":memory:")
    migrate(conn)

    tables = _object_names(conn, "table")
    assert _EXPECTED_TABLES <= tables
    assert {"line_items", "crawl_errors"}.isdisjoint(tables)
    assert {"v_receipt_contents", "v_price_series"} <= _object_names(conn, "view")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_no_duplicated_descriptive_columns() -> None:
    conn = sqlite3.connect(":memory:")
    migrate(conn)

    # shop_address lives only in shops; title/price_type only in products.
    assert "shop_address" not in _columns(conn, "receipts")
    assert "address" in _columns(conn, "shops")
    item_cols = _columns(conn, "receipt_items")
    assert "title" not in item_cols
    assert "price_type" not in item_cols


def test_v_price_series_has_single_price_type_and_title() -> None:
    conn = sqlite3.connect(":memory:")
    migrate(conn)

    cols = _columns(conn, "v_price_series")
    assert "title" in cols
    assert "price_type" in cols
    assert "current_price_type" not in cols
    assert "point_price_type" not in cols


def test_migrate_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    migrate(conn)
    migrate(conn)  # must not raise

    assert _EXPECTED_TABLES <= _object_names(conn, "table")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_foreign_keys_are_enforced() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)

    # A receipt item referencing a non-existent receipt/product must be rejected.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO receipt_items"
            " (receipt_id, line_no, product_id, quantity, amount_cents, unit_price_source)"
            " VALUES ('nope', 0, 999, '1', 100, 'unknown')"
        )


def test_migrate_refreshes_a_stale_view_body() -> None:
    conn = sqlite3.connect(":memory:")
    # Simulate an older DB carrying a v_price_series with a different shape.
    conn.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE VIEW v_price_series AS SELECT 1 AS stale_col;"
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()

    migrate(conn)

    cols = _columns(conn, "v_price_series")
    assert "stale_col" not in cols  # the old body was dropped, not kept
    assert {"product_id", "unit_price_cents", "title"} <= cols


def test_migrate_v1_drops_legacy_and_keeps_meta() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE line_items (x);"
        "CREATE TABLE crawl_errors (x);"
        "CREATE TABLE receipts (x);"
        "CREATE TABLE products (x);"
    )
    conn.execute("INSERT INTO meta VALUES ('last_collect_ts', '123')")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()

    migrate(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    tables = _object_names(conn, "table")
    assert {"line_items", "crawl_errors"}.isdisjoint(tables)  # legacy gone
    assert _EXPECTED_TABLES <= tables  # v2 present
    # meta bookkeeping survives the upgrade.
    surviving = conn.execute(
        "SELECT value FROM meta WHERE key = 'last_collect_ts'"
    ).fetchone()
    assert surviving[0] == "123"
