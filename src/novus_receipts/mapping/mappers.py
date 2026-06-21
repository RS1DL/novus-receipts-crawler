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


def to_number(value: str) -> float | str:
    """Parse a money string (e.g. ``"232.45"``) to a ``float`` for JSON output.

    Non-numeric strings are returned unchanged, so it is safe on fields that may
    occasionally hold something other than a decimal.
    """

    try:
        return float(value)
    except ValueError:
        return value


#: JSON keys whose string values are monetary and should become numbers. Note
#: ``quantity`` is intentionally excluded (it is a count/weight, not money).
_MONEY_KEYS = frozenset(
    {
        "amount",
        "bonus",
        "bonuses_accrued",
        "bonuses_written_off",
        "total_discount_saving",
        "total_promotion_saving",
        "item_price",
        "old_price",
        "discount_amount",
    }
)


class ReceiptMapper:
    """Maps a raw :class:`ReceiptBundle` to a JSON-able dict for consumption.

    The API/DTO layers stay a raw mirror of the responses (Unix-second ``date``,
    money as strings); this mapper is the crawler's seam (PLAN.md §7) where the
    collected data is shaped:

    - every ``date`` (Unix seconds) becomes a human-readable ISO-8601 string in
      ``tz`` (UTC by default);
    - every money field (see ``_MONEY_KEYS``), at any nesting depth, becomes a
      ``float`` instead of a string.

    Other fields (ids, ``check_number``, ``quantity``, ``price_type`` ...) are
    carried through untouched.
    """

    def __init__(self, tz: tzinfo = UTC) -> None:
        self._tz = tz

    def map(self, dto: ReceiptBundle) -> dict[str, Any]:
        """Return ``{"summary": ..., "detail": ...}``, normalised for humans."""

        detail = None if dto.detail is None else self._normalise(dto.detail.model_dump())
        return {
            "summary": self._normalise(dto.summary.model_dump()),
            "detail": detail,
        }

    def _normalise(self, value: Any) -> Any:
        """Recursively render dates ISO-8601 and money fields as numbers."""

        if isinstance(value, dict):
            return {key: self._normalise_field(key, val) for key, val in value.items()}
        if isinstance(value, list):
            return [self._normalise(item) for item in value]
        return value

    def _normalise_field(self, key: str, value: Any) -> Any:
        if key == "date" and isinstance(value, int):
            return format_timestamp(value, self._tz)
        if key in _MONEY_KEYS and isinstance(value, str):
            return to_number(value)
        return self._normalise(value)
