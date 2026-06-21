"""DTOs for the purchase-history list endpoints (PLAN.md §3.3, NOVUS_API.md §4.1).

Three list variants mirror the three Novus endpoints one-to-one:

- (A) ``PurchaseDetailsResponse`` -- ``GET /user/purchases`` (``has_more`` paging).
- (B) ``Purchase2Response`` -- ``GET /user/purchases_2`` (``page``/``total_count``).
- (C) ``ShoppingDetailsResponse`` -- ``GET /v2/user/purchases`` (aggregated view).

Field names equal the JSON keys; the only diverging key is the receipt id, which
the API returns as ``raw_end_time_stamp``. Money fields stay ``str`` and
timestamp/month fields stay ``int`` (Long), per PLAN.md §3.
"""

from __future__ import annotations

from pydantic import Field

from novus_receipts.dto._base import BaseDTO


class PurchaseResponse(BaseDTO):
    """One receipt in a list (variants A/B). Source of the detalization keys.

    The live ``/user/purchases_2`` items carry only ``shop_id``, ``shop_address``,
    ``date``, ``bonus``, ``amount`` and ``check_number``; ``raw_end_time_stamp``
    (the receipt ``id``), ``cash_id`` and ``work_station_id`` are absent there, so
    they are optional. A missing ``work_station_id`` is exactly what makes the
    crawler fall back from ``get_bill`` to ``get_purchase`` for the detalization.
    """

    id: str | None = Field(default=None, alias="raw_end_time_stamp")
    cash_id: int | None = None
    shop_id: str
    amount: str
    bonus: str
    check_number: str
    date: int
    shop_address: str
    work_station_id: str | None = None


class PurchaseOperationsDetails(BaseDTO):
    """A month group bundling its receipts (variants A/B)."""

    month: int
    amount: str
    data: list[PurchaseResponse]


class PurchaseDetailsResponse(BaseDTO):
    """(A) ``GET /user/purchases`` -- ``has_more``-paginated month groups."""

    data: list[PurchaseOperationsDetails]
    has_more: bool


class Purchase2Response(BaseDTO):
    """(B) ``GET /user/purchases_2`` -- ``page``/``total_count``-paginated."""

    data: list[PurchaseOperationsDetails]
    limit: int
    page: int
    total_count: int


class ShoppingResponse(BaseDTO):
    """(C) One receipt in the ``/v2/user/purchases`` view (``id`` is ``int`` here)."""

    id: int
    amount: str
    bonus: str
    cash_id: int
    check_number: str
    date: int
    shop_address: str
    shop_id: str
    work_station_id: str


class ShoppingMonthSummary(BaseDTO):
    """(C) A month summary in the shopping view.

    ``bonuses_accured`` keeps the API's misspelling -- that IS the JSON key.
    """

    month: int
    amount: str
    bonuses_accured: str
    discounts: str
    data: list[ShoppingResponse]


class ShoppingDetailsResponse(BaseDTO):
    """(C) ``GET /v2/user/purchases`` -- month summaries plus full paging fields."""

    data: list[ShoppingMonthSummary]
    has_more: bool
    limit: int
    page: int
    total_count: int
