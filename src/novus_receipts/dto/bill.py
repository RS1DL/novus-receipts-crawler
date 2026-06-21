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

from pydantic import ConfigDict, Field

from novus_receipts.dto._base import BaseDTO


class CouponResponse(BaseDTO):
    """Coupon attached to a receipt; fields are undocumented (PLAN.md §3.4).

    The one exception to the shared ``extra="ignore"`` policy: unknown keys are
    kept (``extra="allow"``) so no coupon data is lost until the shape is known.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


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
    """A receipt line item (used by both detail endpoints).

    Only ``title``/``amount``/``quantity``/``price_type``/``id`` are reliably
    present in the live API; ``item_price``/``old_price``/``image``/``discounts``
    vary by receipt and so are optional. ``rating`` has an unspecified type in
    the API source (PLAN.md §3.4 open question): ``str``, ``float`` or ``None``.
    """

    title: str
    amount: str
    quantity: str
    price_type: str
    id: int
    item_price: str | None = None
    old_price: str | None = None
    image: str | None = None
    rating: str | float | None = None
    discounts: list[GoodDiscount] = Field(default_factory=list)


class PurchaseDetalizationResponse(BaseDTO):
    """Receipt detail from ``/user/purchase_2`` (NOVUS_API.md §4.2 A).

    The live endpoint returns the *rich* shape -- bonus/discount breakdowns,
    savings totals and detailed ``goods`` -- but omits ``id``. So ``id`` is
    optional and the rich collections default to empty when absent. (The
    reverse-engineered spec described a leaner variant A; the real response,
    captured here, mirrors :class:`BillResponse` minus ``id`` and
    ``is_csat_available``.)
    """

    id: int | None = None
    amount: str
    bonuses_accrued: str
    bonuses_written_off: str
    check_number: str
    date: int
    payment_method: str
    shop_address: str
    shop_id: str
    coupons: list[CouponResponse] = Field(default_factory=list)
    goods: list[GoodResponse] = Field(default_factory=list)
    bonuses_details: list[BonusesDetail] = Field(default_factory=list)
    discounts_details: list[DiscountsDetail] = Field(default_factory=list)
    total_discount_saving: str | None = None
    total_promotion_saving: str | None = None


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
