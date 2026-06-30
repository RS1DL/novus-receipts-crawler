"""Deterministic receipt ids (``store.ids``)."""

from __future__ import annotations

import uuid

from novus_receipts.store.ids import receipt_guid


def test_receipt_guid_is_deterministic() -> None:
    assert receipt_guid("42", 1_718_000_000, "123") == receipt_guid("42", 1_718_000_000, "123")


def test_receipt_guid_differs_by_each_key_part() -> None:
    base = receipt_guid("42", 1_718_000_000, "123")
    assert receipt_guid("43", 1_718_000_000, "123") != base
    assert receipt_guid("42", 1_718_000_001, "123") != base
    assert receipt_guid("42", 1_718_000_000, "124") != base


def test_receipt_guid_is_a_uuid_string() -> None:
    value = receipt_guid("42", 1_718_000_000, "123")
    assert str(uuid.UUID(value)) == value  # parses as a canonical UUID
