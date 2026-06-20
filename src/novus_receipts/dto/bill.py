"""Receipt-detail DTOs (PLAN.md §3.4, §3.6; NOVUS_API.md §4.2).

Two detail endpoints mirror the same receipt at different richness:

- ``/user/purchase_2`` -> :class:`PurchaseDetalizationResponse` (variant A).
- ``/v2/user/purchase`` -> :class:`BillResponse` (variant B, richer).

All models subclass :class:`BaseDTO` (``extra="ignore"``, ``populate_by_name``)
and mirror the JSON one-to-one: field names equal snake_case JSON keys, money
fields are ``str``, ``date`` is ``int`` (Long). :class:`CouponResponse` and
:class:`ProfileResponse` are the documented exceptions using ``extra="allow"``
because their fields are not specified in the API source.
"""

from __future__ import annotations

from pydantic import ConfigDict

from novus_receipts.dto._base import BaseDTO


class CouponResponse(BaseDTO):
    """Coupon attached to a receipt; fields are undocumented (PLAN.md §3.4).

    The one exception to the shared ``extra="ignore"`` policy: unknown keys are
    kept (``extra="allow"``) so no coupon data is lost until the shape is known.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class GoodsPurchaseResponse(BaseDTO):
    """A line item in :class:`PurchaseDetalizationResponse` (variant A)."""

    title: str
    amount: str
    quantity: str
    price_type: str
    id: int


class PurchaseDetalizationResponse(BaseDTO):
    """Variant A receipt detail: ``/user/purchase_2`` (NOVUS_API.md §4.2 A)."""

    id: int
    amount: str
    bonuses_accrued: str
    bonuses_written_off: str
    check_number: str
    coupons: list[CouponResponse]
    date: int
    goods: list[GoodsPurchaseResponse]
    payment_method: str
    shop_address: str
    shop_id: str


class BonusesDetail(BaseDTO):
    """Per-good bonus breakdown inside :class:`BillResponse`."""

    amount: str
    goods_title: str


class DiscountsDetail(BaseDTO):
    """Per-discount breakdown inside :class:`BillResponse`."""

    amount: str
    title: str


class GoodDiscount(BaseDTO):
    """A discount applied to a single :class:`GoodResponse` line item."""

    discount_amount: str
    discount_title: str
    discount_type_title: str


class GoodResponse(BaseDTO):
    """A line item in :class:`BillResponse` (variant B).

    ``rating`` has an unspecified type in the API source (PLAN.md §3.4 open
    question) so it accepts ``str``, ``float`` or missing/``None``.
    """

    title: str
    amount: str
    quantity: str
    item_price: str
    old_price: str
    price_type: str
    image: str
    rating: str | float | None = None
    id: int
    discounts: list[GoodDiscount]


class BillResponse(BaseDTO):
    """Variant B receipt detail: ``/v2/user/purchase`` (NOVUS_API.md §4.2 B)."""

    id: int
    amount: str
    date: int
    check_number: str
    shop_id: str
    shop_address: str
    payment_method: str
    bonuses_accrued: str
    bonuses_written_off: str
    bonuses_details: list[BonusesDetail]
    discounts_details: list[DiscountsDetail]
    total_discount_saving: str
    total_promotion_saving: str
    coupons: list[CouponResponse]
    is_csat_available: bool
    goods: list[GoodResponse]


class ProfileResponse(BaseDTO):
    """User profile (NOVUS_API.md §3.2; PLAN.md §3.6).

    Fields are undocumented in the API source, so like :class:`CouponResponse`
    this model uses ``extra="allow"`` to retain whatever the server returns.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)
