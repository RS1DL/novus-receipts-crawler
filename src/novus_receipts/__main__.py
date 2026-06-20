"""CLI entrypoint: ``python -m novus_receipts`` (PLAN.md §5, TASKS.md T7.3).

Runs :class:`PurchaseHistoryJob` and serialises the resulting
:class:`CrawlResult` to stdout as JSON. ``CrawlResult`` is a dataclass holding
pydantic DTOs (and, for item errors, plain exceptions), so it cannot be dumped
directly; :func:`_result_to_jsonable` walks it into a JSON-able dict using each
DTO's ``model_dump``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from novus_receipts.crawler.results import CrawlResult, ReceiptBundle, ReceiptDetail
from novus_receipts.entrypoint import PurchaseHistoryJob


def _detail_to_jsonable(detail: ReceiptDetail) -> dict[str, Any] | None:
    """Dump a receipt detail DTO, or ``None`` when no detail was fetched."""

    return None if detail is None else detail.model_dump()


def _bundle_to_jsonable(bundle: ReceiptBundle) -> dict[str, Any]:
    """Dump one :class:`ReceiptBundle` (summary DTO + optional detail DTO)."""

    return {
        "summary": bundle.summary.model_dump(),
        "detail": _detail_to_jsonable(bundle.detail),
    }


def _result_to_jsonable(result: CrawlResult) -> dict[str, Any]:
    """Turn a :class:`CrawlResult` into a JSON-serialisable dict.

    Pydantic DTOs go through ``model_dump``; per-item errors keep only the
    failing check's id and a string rendering of the exception (an ``Exception``
    is not JSON-serialisable on its own).
    """

    return {
        "receipts": [_bundle_to_jsonable(b) for b in result.receipts],
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

    result = PurchaseHistoryJob().run()
    json.dump(_result_to_jsonable(result), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
