"""Sidecar audit log for non-fatal crawl errors.

Crawl errors are *operational* data, not business data, so they live OUTSIDE the
receipts database. One JSON object per line (JSONL): append-only, greppable, and
never joined to receipts. It sits next to the database at
``<db_path>.errors.jsonl``.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from novus_receipts.crawler.results import CrawlItemError


def default_error_log_path(db_path: str | os.PathLike[str]) -> str:
    """``foo.db`` -> ``foo.db.errors.jsonl`` (sits beside the database)."""

    return f"{os.fspath(db_path)}.errors.jsonl"


class CrawlErrorLog:
    """Append-only JSONL sink for :class:`CrawlItemError` records."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = os.fspath(path)

    def record(self, err: CrawlItemError, *, now_ts: int) -> None:
        """Append one crawl error as a JSON line."""

        check = err.check
        row = {
            "occurred_ts": now_ts,
            "check_number": check.check_number,
            "date": check.date,
            "shop_id": check.shop_id,
            "error_type": type(err.error).__name__,
            "error_text": str(err.error),
        }
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
