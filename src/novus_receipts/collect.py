"""Incremental collection job + CLI: ``python -m novus_receipts.collect``.

Composes :class:`~novus_receipts.entrypoint.PurchaseHistoryJob` (unchanged) with
the :class:`~novus_receipts.store.sqlite_store.ReceiptStore`: it resolves an
incremental ``from_`` from the stored watermark, runs the crawl with **no
mapper** (so it gets typed ``ReceiptBundle`` objects), and upserts every receipt.
Idempotent and safe to re-run / schedule -- see the README for cron / launchd.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from novus_receipts.__main__ import parse_from
from novus_receipts.config import AppConfig
from novus_receipts.entrypoint import PurchaseHistoryJob
from novus_receipts.store.error_log import CrawlErrorLog, default_error_log_path
from novus_receipts.store.sqlite_store import ReceiptStore


@dataclass(frozen=True)
class CollectStats:
    """Outcome of one :meth:`CollectJob.run`."""

    receipts_upserted: int
    products_touched: int
    line_items_written: int
    errors_recorded: int
    watermark_before: int | None
    from_used: datetime | None


class CollectJob:
    """Run an incremental collection into the SQLite store.

    ``config`` defaults to :meth:`AppConfig.from_env`. ``job`` and ``store`` are
    injectable for tests (e.g. a mock-transport job and an in-memory store);
    ``clock`` supplies the ingest timestamp (injected so tests stay deterministic).
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        job: PurchaseHistoryJob | None = None,
        store: ReceiptStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config if config is not None else AppConfig.from_env()
        self._tz = ZoneInfo(self._config.timezone)
        self._job = job
        self._store = store
        self._clock = clock if clock is not None else self._default_clock

    def _default_clock(self) -> datetime:
        return datetime.now(self._tz)

    def run(self, *, from_: datetime | None = None, full: bool = False) -> CollectStats:
        """Resolve the cutoff, crawl, and upsert everything; return the counts.

        The cutoff is, in precedence order: an explicit ``from_``; ``None`` (full
        history) when ``full``; otherwise ``watermark - collect_overlap_s`` so a
        small recent window is re-pulled to catch late / same-day receipts (the
        overlap is harmless -- every write is an idempotent upsert).
        """

        store = self._store if self._store is not None else ReceiptStore.open(
            self._config.db_path, tz=self._tz
        )
        owns_store = self._store is None
        try:
            watermark = store.get_watermark()
            effective_from = self._resolve_from(from_, full=full, watermark=watermark)

            runner = self._job if self._job is not None else PurchaseHistoryJob(self._config)
            result = runner.run(from_=effective_from)  # no mapper -> ReceiptBundle items

            now_ts = int(self._clock().timestamp())
            receipts_upserted = 0
            products: set[int] = set()
            line_items_written = 0
            for bundle in result.receipts:
                store.upsert_bundle(bundle, now_ts=now_ts)
                receipts_upserted += 1
                detail = bundle.detail
                if detail is not None:
                    for good in detail.goods:
                        products.add(good.id)
                        line_items_written += 1

            # Crawl errors are operational, not business data: they go to a
            # sidecar JSONL log next to the DB, never into the store.
            error_log = CrawlErrorLog(default_error_log_path(self._config.db_path))
            for err in result.errors:
                error_log.record(err, now_ts=now_ts)

            store.set_last_collect_ts(now_ts)

            return CollectStats(
                receipts_upserted=receipts_upserted,
                products_touched=len(products),
                line_items_written=line_items_written,
                errors_recorded=len(result.errors),
                watermark_before=watermark,
                from_used=effective_from,
            )
        finally:
            if owns_store:
                store.close()

    def _resolve_from(
        self, from_: datetime | None, *, full: bool, watermark: int | None
    ) -> datetime | None:
        if from_ is not None:
            return from_
        if full or watermark is None:
            return None
        start = max(0, watermark - self._config.collect_overlap_s)
        return datetime.fromtimestamp(start, self._tz)


def _summary_line(stats: CollectStats) -> str:
    cutoff = stats.from_used.isoformat() if stats.from_used is not None else "full history"
    return (
        f"collected {stats.receipts_upserted} receipts, "
        f"{stats.line_items_written} line items, "
        f"{stats.products_touched} products; "
        f"{stats.errors_recorded} errors; from={cutoff}"
    )


def main(argv: list[str] | None = None) -> int:
    """Run an incremental collection, print a one-line summary, return ``0``.

    ``--from WHEN`` overrides the incremental cutoff (a duration like ``7d`` or an
    ISO date/datetime); ``--full`` ignores the watermark and re-scans all history;
    ``--db PATH`` overrides ``NOVUS_DB_PATH``.
    """

    parser = argparse.ArgumentParser(
        prog="novus_receipts.collect",
        description="Incrementally collect Novus receipts into a local SQLite store.",
    )
    parser.add_argument(
        "--from",
        dest="from_",
        default=None,
        metavar="WHEN",
        help=(
            "Override the cutoff: a duration before now (e.g. 7d, 2w, 24h) or an "
            "ISO date/datetime (e.g. 2026-06-10). Default resumes from the last run."
        ),
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Ignore the stored watermark and re-scan the full history.",
    )
    parser.add_argument(
        "--db",
        dest="db",
        default=None,
        metavar="PATH",
        help="SQLite file to write to (overrides NOVUS_DB_PATH).",
    )
    args = parser.parse_args(argv)

    config = AppConfig.from_env()
    if args.db is not None:
        config.db_path = args.db
    tz = ZoneInfo(config.timezone)
    from_ = (
        None
        if args.from_ is None
        else parse_from(args.from_, now=datetime.now(tz), tz=tz)
    )

    stats = CollectJob(config).run(from_=from_, full=args.full)
    print(_summary_line(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
