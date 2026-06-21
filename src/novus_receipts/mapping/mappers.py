"""Mapping extension point: ``Mapper`` protocol + concrete mappers.

This is the single, isolated seam where raw Novus DTOs are turned into the
shape we want to consume, keeping the API/DTO layers a pure mirror of the HTTP
responses (PLAN.md §7). Two implementations:

- :class:`IdentityMapper` -- 1:1 passthrough (returns the DTO unchanged).
- :class:`ReceiptMapper` -- renders a :class:`ReceiptBundle` as a JSON-able dict
  with human-readable ISO-8601 ``date`` fields instead of raw Unix seconds. This
  is where further normalisation (money ``str -> Decimal``, summary+detail
  merging) will live.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from novus_receipts.crawler.results import ReceiptBundle

TIn = TypeVar("TIn", contravariant=True)
TOut = TypeVar("TOut", covariant=True)


@runtime_checkable
class Mapper(Protocol[TIn, TOut]):
    """Transforms an input DTO into some output (domain) representation."""

    def map(self, dto: TIn) -> TOut:
        """Map ``dto`` to its output representation."""
        ...


_T = TypeVar("_T")


class IdentityMapper:
    """Returns its argument unchanged (current 1:1 passthrough mapping)."""

    def map(self, dto: _T) -> _T:
        """Return ``dto`` exactly as received."""
        return dto


def format_timestamp(timestamp: int, tz: tzinfo = UTC) -> str:
    """Render a Unix-second timestamp as an ISO-8601 string in ``tz``.

    e.g. ``1718000000 -> "2024-06-10T06:13:20+00:00"`` (UTC).
    """

    return datetime.fromtimestamp(timestamp, tz=tz).isoformat()


class ReceiptMapper:
    """Maps a raw :class:`ReceiptBundle` to a JSON-able dict for consumption.

    The API/DTO layers stay a raw mirror of the responses (Unix-second ``date``,
    money as strings); this mapper is the crawler's seam (PLAN.md §7) where the
    collected data is shaped. Currently it renders every ``date`` (Unix seconds)
    as a human-readable ISO-8601 string in ``tz`` (UTC by default); future
    transforms (money ``str -> Decimal``, flattening) belong here too.
    """

    def __init__(self, tz: tzinfo = UTC) -> None:
        self._tz = tz

    def map(self, dto: ReceiptBundle) -> dict[str, Any]:
        """Return ``{"summary": ..., "detail": ...}`` with ISO-8601 dates."""

        detail = None if dto.detail is None else self._with_iso_dates(dto.detail.model_dump())
        return {
            "summary": self._with_iso_dates(dto.summary.model_dump()),
            "detail": detail,
        }

    def _with_iso_dates(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return ``data`` with an integer ``date`` replaced by its ISO string."""

        timestamp = data.get("date")
        if isinstance(timestamp, int):
            return {**data, "date": format_timestamp(timestamp, self._tz)}
        return data
