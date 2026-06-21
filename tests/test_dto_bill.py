"""Tests for receipt-detail DTOs (TASKS.md T3.7, T3.8, T3.11; PLAN.md §3.4, §3.6).

Covers deserialization of representative JSON for both detail variants plus the
profile model. Exercises the documented edge cases: ``CouponResponse`` keeps
undocumented keys (``extra="allow"``), ``GoodResponse.rating`` accepts ``str``,
``float`` and missing/``None``, and nested ``goods``/``discounts`` parse.
"""

from __future__ import annotations

from novus_receipts.dto.bill import (
    BillResponse,
    BonusesDetail,
    CouponResponse,
    DiscountsDetail,
    GoodDiscount,
    GoodResponse,
    ProfileResponse,
    PurchaseDetalizationResponse,
)


def _purchase_detalization_json() -> dict[str, object]:
    """Representative ``/user/purchase_2`` payload (variant A)."""
    return {
        "id": 101,
        "amount": "248.50",
        "bonuses_accrued": "12.00",
        "bonuses_written_off": "0.00",
        "check_number": "42",
        "coupons": [{"code": "ABC", "discount": "5.00", "weird_field": [1, 2]}],
        "date": 1718000000,
        "goods": [
            {
                "title": "Milk 1L",
                "amount": "35.98",
                "quantity": "2",
                "price_type": "regular",
                "id": 555,
            },
        ],
        "payment_method": "card",
        "shop_address": "Kyiv, Main St 1",
        "shop_id": "123",
    }


def _bill_json() -> dict[str, object]:
    """Representative ``/v2/user/purchase`` payload (variant B)."""
    return {
        "id": 202,
        "amount": "500.00",
        "date": 1718000000,
        "check_number": "77",
        "shop_id": "123",
        "shop_address": "Kyiv, Main St 1",
        "payment_method": "cash",
        "bonuses_accrued": "25.00",
        "bonuses_written_off": "5.00",
        "bonuses_details": [{"amount": "25.00", "goods_title": "Coffee"}],
        "discounts_details": [{"amount": "10.00", "title": "Loyalty"}],
        "total_discount_saving": "10.00",
        "total_promotion_saving": "3.50",
        "coupons": [{"id": 9, "undocumented": {"nested": True}}],
        "is_csat_available": True,
        "goods": [
            {
                "title": "Coffee 250g",
                "amount": "120.00",
                "quantity": "1",
                "item_price": "120.00",
                "old_price": "150.00",
                "price_type": "promo",
                "image": "https://img/coffee.png",
                "rating": "4.5",
                "id": 777,
                "discounts": [
                    {
                        "discount_amount": "30.00",
                        "discount_title": "Promo -20%",
                        "discount_type_title": "promotion",
                    }
                ],
            },
        ],
    }


# --- variant A: PurchaseDetalizationResponse -------------------------------


def test_purchase_detalization_parses_all_fields() -> None:
    model = PurchaseDetalizationResponse.model_validate(_purchase_detalization_json())

    assert model.id == 101
    assert model.amount == "248.50"
    assert model.bonuses_accrued == "12.00"
    assert model.bonuses_written_off == "0.00"
    assert model.check_number == "42"
    assert model.date == 1718000000
    assert model.payment_method == "card"
    assert model.shop_address == "Kyiv, Main St 1"
    assert model.shop_id == "123"


def test_purchase_detalization_nested_goods_parse() -> None:
    model = PurchaseDetalizationResponse.model_validate(_purchase_detalization_json())

    assert len(model.goods) == 1
    good = model.goods[0]
    assert isinstance(good, GoodResponse)
    assert good.title == "Milk 1L"
    assert good.amount == "35.98"
    assert good.quantity == "2"
    assert good.price_type == "regular"
    assert good.id == 555
    # Rich fields absent in this lean item default to None / [].
    assert good.item_price is None
    assert good.discounts == []


def test_purchase_detalization_coupons_parse() -> None:
    model = PurchaseDetalizationResponse.model_validate(_purchase_detalization_json())

    assert len(model.coupons) == 1
    assert isinstance(model.coupons[0], CouponResponse)


def test_purchase_detalization_parses_live_rich_shape_without_id() -> None:
    # The live /user/purchase_2 omits `id` and returns the rich BillResponse-like
    # shape with detailed goods; this must parse and capture the extra fields.
    live = {
        "amount": "232.45",
        "bonuses_accrued": "2.32",
        "bonuses_written_off": "0.00",
        "check_number": "1118.29-0",
        "date": 1781690422,
        "payment_method": "card",
        "shop_address": "м. Київ, Львівська площа, 8Б",
        "shop_id": "7016",
        "coupons": [],
        "bonuses_details": [{"amount": "2.32", "goods_title": "Кава"}],
        "discounts_details": [{"amount": "5.00", "title": "Акція"}],
        "total_discount_saving": "5.00",
        "total_promotion_saving": "0.00",
        "goods": [
            {
                "title": "Кава",
                "amount": "120.00",
                "quantity": "1",
                "price_type": "regular",
                "id": 1,
                "item_price": "120.00",
                "old_price": "150.00",
                "image": "https://img/x.png",
                "discounts": [],
            }
        ],
    }

    model = PurchaseDetalizationResponse.model_validate(live)

    assert model.id is None
    assert model.total_discount_saving == "5.00"
    assert len(model.bonuses_details) == 1
    assert model.goods[0].item_price == "120.00"


# --- CouponResponse: extra="allow" -----------------------------------------


def test_coupon_response_config_is_allow() -> None:
    assert CouponResponse.model_config["extra"] == "allow"


def test_coupon_response_keeps_unknown_keys() -> None:
    coupon = CouponResponse.model_validate(
        {"code": "SAVE10", "discount": "10.00", "mystery": [1, 2, 3]}
    )

    dumped = coupon.model_dump()
    assert dumped["code"] == "SAVE10"
    assert dumped["discount"] == "10.00"
    assert dumped["mystery"] == [1, 2, 3]


def test_coupon_response_empty_object() -> None:
    coupon = CouponResponse.model_validate({})

    assert coupon.model_dump() == {}


# --- variant B: BillResponse -----------------------------------------------


def test_bill_response_parses_all_scalar_fields() -> None:
    model = BillResponse.model_validate(_bill_json())

    assert model.id == 202
    assert model.amount == "500.00"
    assert model.date == 1718000000
    assert model.check_number == "77"
    assert model.shop_id == "123"
    assert model.shop_address == "Kyiv, Main St 1"
    assert model.payment_method == "cash"
    assert model.bonuses_accrued == "25.00"
    assert model.bonuses_written_off == "5.00"
    assert model.total_discount_saving == "10.00"
    assert model.total_promotion_saving == "3.50"
    assert model.is_csat_available is True


def test_bill_response_bonuses_and_discounts_details_parse() -> None:
    model = BillResponse.model_validate(_bill_json())

    assert model.bonuses_details == [BonusesDetail(amount="25.00", goods_title="Coffee")]
    assert model.discounts_details == [DiscountsDetail(amount="10.00", title="Loyalty")]


def test_bill_response_coupons_keep_unknown_keys() -> None:
    model = BillResponse.model_validate(_bill_json())

    assert len(model.coupons) == 1
    dumped = model.coupons[0].model_dump()
    assert dumped["id"] == 9
    assert dumped["undocumented"] == {"nested": True}


def test_bill_response_nested_goods_and_discounts_parse() -> None:
    model = BillResponse.model_validate(_bill_json())

    assert len(model.goods) == 1
    good = model.goods[0]
    assert isinstance(good, GoodResponse)
    assert good.title == "Coffee 250g"
    assert good.item_price == "120.00"
    assert good.old_price == "150.00"
    assert good.image == "https://img/coffee.png"
    assert good.id == 777

    assert len(good.discounts) == 1
    discount = good.discounts[0]
    assert isinstance(discount, GoodDiscount)
    assert discount.discount_amount == "30.00"
    assert discount.discount_title == "Promo -20%"
    assert discount.discount_type_title == "promotion"


# --- GoodResponse.rating: str | float | None -------------------------------


def _good_json(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Item",
        "amount": "10.00",
        "quantity": "1",
        "item_price": "10.00",
        "old_price": "12.00",
        "price_type": "regular",
        "image": "https://img/item.png",
        "id": 1,
        "discounts": [],
    }
    base.update(overrides)
    return base


def test_good_rating_accepts_str() -> None:
    good = GoodResponse.model_validate(_good_json(rating="4.5"))

    assert good.rating == "4.5"


def test_good_rating_accepts_float() -> None:
    good = GoodResponse.model_validate(_good_json(rating=4.5))

    assert good.rating == 4.5


def test_good_rating_accepts_explicit_null() -> None:
    good = GoodResponse.model_validate(_good_json(rating=None))

    assert good.rating is None


def test_good_rating_defaults_to_none_when_missing() -> None:
    good = GoodResponse.model_validate(_good_json())

    assert good.rating is None


def test_good_with_empty_discounts() -> None:
    good = GoodResponse.model_validate(_good_json())

    assert good.discounts == []


# --- ProfileResponse: extra="allow" (T3.11) --------------------------------


def test_profile_response_config_is_allow() -> None:
    assert ProfileResponse.model_config["extra"] == "allow"


def test_profile_response_keeps_arbitrary_fields() -> None:
    profile = ProfileResponse.model_validate(
        {"first_name": "Roman", "phone": "380000000000", "extra": {"a": 1}}
    )

    dumped = profile.model_dump()
    assert dumped["first_name"] == "Roman"
    assert dumped["phone"] == "380000000000"
    assert dumped["extra"] == {"a": 1}


def test_profile_response_empty_object() -> None:
    profile = ProfileResponse.model_validate({})

    assert profile.model_dump() == {}
