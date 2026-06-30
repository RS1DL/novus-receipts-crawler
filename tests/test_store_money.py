"""Money / quantity parsing for the store (``store.money``).

Money becomes integer minor units (kopiykas) so aggregation never drifts;
quantity stays a float (it can be fractional). Absent/garbage values become
``None`` rather than a misleading ``0``.
"""

from __future__ import annotations

from novus_receipts.store.money import parse_cents, parse_qty


def test_parse_cents_decimal_strings() -> None:
    assert parse_cents("232.45") == 23245
    assert parse_cents("0") == 0
    assert parse_cents("0.00") == 0
    assert parse_cents("9.87") == 987
    assert parse_cents("12") == 1200


def test_parse_cents_rounds_half_up() -> None:
    assert parse_cents("232.455") == 23246
    assert parse_cents("0.005") == 1


def test_parse_cents_absent_or_garbage_is_none() -> None:
    assert parse_cents(None) is None
    assert parse_cents("") is None
    assert parse_cents("   ") is None
    assert parse_cents("abc") is None


def test_parse_cents_non_finite_is_none() -> None:
    # Decimal parses these; they must not blow up int() (OverflowError/ValueError).
    assert parse_cents("Infinity") is None
    assert parse_cents("-Infinity") is None
    assert parse_cents("NaN") is None


def test_parse_qty_floats() -> None:
    assert parse_qty("0.738") == 0.738
    assert parse_qty("2") == 2.0
    assert parse_qty(" 1.5 ") == 1.5


def test_parse_qty_garbage_is_none() -> None:
    assert parse_qty("") is None
    assert parse_qty("abc") is None


def test_parse_qty_non_finite_is_none() -> None:
    assert parse_qty("inf") is None
    assert parse_qty("nan") is None
    assert parse_qty("1e400") is None  # overflows float to inf
