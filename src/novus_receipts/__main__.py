"""CLI entrypoint: ``python -m novus_receipts`` (PLAN.md §5, TASKS.md T7.3).

Runs :class:`PurchaseHistoryJob` with :class:`ReceiptMapper` (the crawler's
mapping seam, PLAN.md §7), which yields JSON-able receipt dicts with
human-readable ISO-8601 dates, and serialises the result to stdout as JSON.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from novus_receipts.crawler.results import CrawlResult
from novus_receipts.dto.bonuses import UserBonusResponse
from novus_receipts.entrypoint import PurchaseHistoryJob
from novus_receipts.mapping.mappers import ReceiptMapper, to_number

_DURATION_RE = re.compile(r"^(\d+)([smhdw])$")
_DURATION_UNITS = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def parse_from(value: str, *, now: datetime, tz: tzinfo) -> datetime:
    """Parse a ``--from`` value into a timezone-aware cutoff datetime.

    Accepts either a relative duration before ``now`` -- ``"7d"``, ``"2w"``,
    ``"24h"``, ``"30m"``, ``"45s"`` -- or an absolute ISO date / datetime
    (``"2026-06-10"`` or ``"2026-06-10T13:00"``). A date or naive datetime is
    interpreted in ``tz``.
    """

    match = _DURATION_RE.match(value.strip())
    if match:
        amount = int(match.group(1))
        unit = _DURATION_UNITS[match.group(2)]
        return now - timedelta(**{unit: amount})
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def _bonuses_to_jsonable(bonuses: UserBonusResponse | None) -> dict[str, Any] | None:
    """Dump the balance DTO with its money ``data`` field as a number."""

    if bonuses is None:
        return None
    dumped = bonuses.model_dump()
    if isinstance(dumped.get("data"), str):
        dumped["data"] = to_number(dumped["data"])
    return dumped


def _result_to_jsonable(result: CrawlResult[Any]) -> dict[str, Any]:
    """Turn a :class:`CrawlResult` into a JSON-serialisable dict.

    ``receipts`` are already JSON-able dicts produced by :class:`ReceiptMapper`
    (ISO dates, money as numbers). The bonus balance is normalised the same way;
    per-item errors keep only the failing check's number and a string rendering
    of the exception (an ``Exception`` is not JSON-serialisable on its own).
    """

    return {
        "receipts": list(result.receipts),
        "current_bonuses": _bonuses_to_jsonable(result.current_bonuses),
        "pages_fetched": result.pages_fetched,
        "total_count": result.total_count,
        "errors": [
            {"check_number": e.check.check_number, "error": str(e.error)}
            for e in result.errors
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """Run the job, print the serialised result to stdout, return ``0``.

    ``--from WHEN`` limits collection to receipts on/after ``WHEN`` (a duration
    like ``7d`` before now, or an ISO date), stopping pagination early so old
    pages and their details are never fetched.
    """

    parser = argparse.ArgumentParser(
        prog="novus_receipts",
        description="Collect Novus receipts/purchases/bonuses for the configured account.",
    )
    parser.add_argument(
        "--from",
        dest="from_",
        default=None,
        metavar="WHEN",
        help=(
            "Only collect receipts on/after WHEN: a duration before now "
            "(e.g. 7d, 2w, 24h) or an ISO date/datetime (e.g. 2026-06-10). "
            "Stops paging early once a page is fully older."
        ),
    )
    args = parser.parse_args(argv)

    job = PurchaseHistoryJob()
    tz = ZoneInfo(job.config.timezone)
    from_ = (
        None
        if args.from_ is None
        else parse_from(args.from_, now=datetime.now(tz), tz=tz)
    )
    result = job.run(from_=from_, mapper=ReceiptMapper(tz=tz))
    # ensure_ascii=False keeps Cyrillic (and other non-ASCII) human-readable in
    # the output instead of escaping it to \uXXXX.
    json.dump(_result_to_jsonable(result), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
