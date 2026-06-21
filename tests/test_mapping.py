"""T6.1 — ``Mapper`` protocol + ``IdentityMapper`` + ``ReceiptMapper`` (PLAN.md §7)."""

from __future__ import annotations

from datetime import UTC, timedelta, timezone

from novus_receipts.crawler.results import ReceiptBundle
from novus_receipts.dto.bill import PurchaseDetalizationResponse
from novus_receipts.dto.purchases import PurchaseResponse
from novus_receipts.mapping.mappers import (
    IdentityMapper,
    Mapper,
    ReceiptMapper,
    format_timestamp,
)


def test_identity_mapper_returns_same_object() -> None:
    obj = object()
    assert IdentityMapper().map(obj) is obj


def test_identity_mapper_preserves_various_payloads() -> None:
    mapper = IdentityMapper()
    payload = {"id": "abc", "amount": "12.34"}
    assert mapper.map(payload) is payload
    assert mapper.map(42) == 42
    assert mapper.map(None) is None


def test_identity_mapper_satisfies_mapper_protocol() -> None:
    assert isinstance(IdentityMapper(), Mapper)


def test_mapper_protocol_rejects_object_without_map() -> None:
    class NotAMapper:
        pass

    assert not isinstance(NotAMapper(), Mapper)


# --- format_timestamp -------------------------------------------------------


def test_format_timestamp_renders_iso_utc() -> None:
    assert format_timestamp(1718000000) == "2024-06-10T06:13:20+00:00"


def test_format_timestamp_respects_timezone() -> None:
    # Same instant rendered in a +03:00 zone (e.g. Kyiv summer time).
    plus3 = timezone(timedelta(hours=3))
    assert format_timestamp(1718000000, tz=plus3) == "2024-06-10T09:13:20+03:00"


# --- ReceiptMapper ----------------------------------------------------------


def _bundle(*, with_detail: bool) -> ReceiptBundle:
    summary = PurchaseResponse.model_validate(
        {
            "shop_id": "7016",
            "shop_address": "Київ",
            "date": 1781690422,
            "bonus": "2.32",
            "amount": "232.45",
            "check_number": "1118.29-0",
        }
    )
    detail = None
    if with_detail:
        detail = PurchaseDetalizationResponse.model_validate(
            {
                "amount": "232.45",
                "bonuses_accrued": "2.32",
                "bonuses_written_off": "0.00",
                "check_number": "1118.29-0",
                "date": 1781690422,
                "payment_method": "Готівка",
                "shop_address": "Київ",
                "shop_id": "7016",
                "goods": [],
            }
        )
    return ReceiptBundle(summary=summary, detail=detail)


def test_receipt_mapper_renders_summary_and_detail_dates_as_iso() -> None:
    mapped = ReceiptMapper(tz=UTC).map(_bundle(with_detail=True))

    assert mapped["summary"]["date"] == "2026-06-17T10:00:22+00:00"
    assert mapped["detail"]["date"] == "2026-06-17T10:00:22+00:00"
    # Non-date fields are carried through untouched (still raw strings).
    assert mapped["summary"]["amount"] == "232.45"
    assert mapped["detail"]["payment_method"] == "Готівка"


def test_receipt_mapper_handles_missing_detail() -> None:
    mapped = ReceiptMapper().map(_bundle(with_detail=False))

    assert mapped["detail"] is None
    assert mapped["summary"]["date"] == "2026-06-17T10:00:22+00:00"


def test_receipt_mapper_satisfies_mapper_protocol() -> None:
    assert isinstance(ReceiptMapper(), Mapper)
