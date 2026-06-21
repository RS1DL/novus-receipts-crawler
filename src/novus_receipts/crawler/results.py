"""Crawler result containers (PLAN.md §4.1).

These are deliberately *raw DTOs wrapped in simple frozen dataclasses* with no
transformation, as the task requires. Domain mapping (str money -> Decimal,
timestamp normalisation, merging ``summary`` + ``detail`` into one entity) is a
later concern isolated in ``mapping/`` (PLAN.md §7).
"""

from __future__ import annotations

from dataclasses import dataclass

from novus_receipts.dto.bill import BillResponse, PurchaseDetalizationResponse
from novus_receipts.dto.bonuses import UserBonusResponse
from novus_receipts.dto.purchases import PurchaseResponse

#: A receipt's detalization: the richer ``BillResponse`` (``/v2/user/purchase``),
#: the ``PurchaseDetalizationResponse`` fallback (``/user/purchase_2``), or
#: ``None`` when the detail could not be fetched.
ReceiptDetail = BillResponse | PurchaseDetalizationResponse | None


@dataclass(frozen=True)
class ReceiptBundle:
    """One receipt: its list-summary plus the fetched detail (``None`` on failure)."""

    summary: PurchaseResponse
    detail: ReceiptDetail


@dataclass(frozen=True)
class CrawlItemError:
    """A non-fatal per-receipt failure: the check and the exception that broke it."""

    check: PurchaseResponse
    error: Exception


@dataclass(frozen=True)
class CrawlResult[R]:
    """The full crawl output: receipts, bonus balance, counters and item errors.

    Generic over the receipt type ``R``: :class:`ReceiptBundle` for the raw /
    identity path, or a mapper's output type (e.g. ``dict`` from
    :class:`~novus_receipts.mapping.mappers.ReceiptMapper`).
    """

    receipts: list[R]
    current_bonuses: UserBonusResponse | None
    pages_fetched: int
    total_count: int | None
    errors: list[CrawlItemError]
