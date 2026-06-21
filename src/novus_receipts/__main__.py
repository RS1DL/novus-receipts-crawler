"""CLI entrypoint: ``python -m novus_receipts`` (PLAN.md §5, TASKS.md T7.3).

Runs :class:`PurchaseHistoryJob` with :class:`ReceiptMapper` (the crawler's
mapping seam, PLAN.md §7), which yields JSON-able receipt dicts with
human-readable ISO-8601 dates, and serialises the result to stdout as JSON.
"""

from __future__ import annotations

import json
import sys
from typing import Any
from zoneinfo import ZoneInfo

from novus_receipts.crawler.results import CrawlResult
from novus_receipts.entrypoint import PurchaseHistoryJob
from novus_receipts.mapping.mappers import ReceiptMapper


def _result_to_jsonable(result: CrawlResult[Any]) -> dict[str, Any]:
    """Turn a :class:`CrawlResult` into a JSON-serialisable dict.

    ``receipts`` are already JSON-able dicts produced by :class:`ReceiptMapper`.
    The bonus balance DTO goes through ``model_dump``; per-item errors keep only
    the failing check's number and a string rendering of the exception (an
    ``Exception`` is not JSON-serialisable on its own).
    """

    return {
        "receipts": list(result.receipts),
        "current_bonuses": (
            None
            if result.current_bonuses is None
            else result.current_bonuses.model_dump()
        ),
        "pages_fetched": result.pages_fetched,
        "total_count": result.total_count,
        "errors": [
            {"check_number": e.check.check_number, "error": str(e.error)}
            for e in result.errors
        ],
    }


def main() -> int:
    """Run the job, print the serialised result to stdout, return ``0``."""

    job = PurchaseHistoryJob()
    mapper = ReceiptMapper(tz=ZoneInfo(job.config.timezone))
    result = job.run(mapper=mapper)
    # ensure_ascii=False keeps Cyrillic (and other non-ASCII) human-readable in
    # the output instead of escaping it to \uXXXX.
    json.dump(_result_to_jsonable(result), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
