"""Parse Novus money / quantity strings for storage.

The API returns money as decimal strings (``"232.45"``). To avoid float drift on
aggregation the store keeps money as **integer minor units** (kopiykas), so
:func:`parse_cents` converts ``"232.45"`` -> ``23245`` via :class:`~decimal.Decimal`.
Quantity is *not* money -- it can be fractional (``"0.738"`` kg) -- so it is kept
verbatim as text alongside a derived float from :func:`parse_qty` for arithmetic.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_CENTS = Decimal(100)


def parse_cents(value: str | None) -> int | None:
    """Parse a money string into integer minor units, or ``None``.

    ``"232.45"`` -> ``23245``; ``"0"`` -> ``0``. ``None``, an empty/blank string
    or a non-numeric value -> ``None`` (so an absent optional field stays absent
    rather than becoming a misleading ``0``). Half-up rounding to whole cents.
    """

    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite():  # reject "Infinity" / "NaN" from the untrusted API
        return None
    return int((amount * _CENTS).to_integral_value(rounding=ROUND_HALF_UP))


def parse_qty(value: str) -> float | None:
    """Parse a quantity string into a float, or ``None`` if unparseable.

    ``"0.738"`` -> ``0.738``; ``"2"`` -> ``2.0``. The raw string is stored
    verbatim too; this is only for derived math (e.g. amount / quantity).
    """

    text = value.strip()
    if not text:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    if not math.isfinite(result):  # reject inf / nan (e.g. "1e400")
        return None
    return result
