"""Persistence layer: a SQLite store for collected receipts and price history.

The crawler returns data in memory; this package gives it a durable home so the
``collect`` job can build a history over time and watch product prices change.

- :mod:`novus_receipts.store.money` -- parse Novus money/quantity strings.
- :mod:`novus_receipts.store.schema` -- the SQLite schema + idempotent migration.
- :mod:`novus_receipts.store.sqlite_store` -- :class:`ReceiptStore`, which upserts
  a :class:`~novus_receipts.crawler.results.ReceiptBundle` (header + products +
  line items / price observations).
"""

from __future__ import annotations

from novus_receipts.store.sqlite_store import ReceiptStore

__all__ = ["ReceiptStore"]
