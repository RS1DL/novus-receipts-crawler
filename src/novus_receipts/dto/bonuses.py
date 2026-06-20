"""Bonus DTOs mirroring Novus ``/user/bonuses*`` responses (PLAN.md §3.5).

Models are a one-to-one mirror of the JSON (NOVUS_API.md §4.3): field names equal
the snake_case JSON keys; the only diverging key is ``type_alias`` on a single
bonus operation, exposed as ``alias`` via ``Field(alias=...)``. Money fields are
``str`` and timestamp fields (``date``, ``month``) are ``int`` (Long), per §3.
"""

from __future__ import annotations

from pydantic import Field

from novus_receipts.dto._base import BaseDTO


class UserBonusResponse(BaseDTO):
    """Current bonus balance from ``GET /user/bonuses/current``."""

    data: str
    data_long: int


class BonusResponse(BaseDTO):
    """A single bonus operation from the bonus history.

    The JSON key ``type_alias`` is exposed as the ``alias`` field; all other
    fields mirror their JSON keys verbatim.
    """

    bonus: str
    check_number: str
    work_station_id: str
    date: int
    is_authorization_bonus: bool
    is_referral_bonus: bool
    shop_address: str
    type_id: int
    type_title: str
    alias: str = Field(alias="type_alias")
    purchase_id: int
    cash_id: int
    receipt_id: str
    shop_id: str


class BonusOperationResponse(BaseDTO):
    """Bonus operations grouped by month."""

    month: int
    amount: str
    data: list[BonusResponse]


class BonusesResponse(BaseDTO):
    """Paginated bonus history from ``GET /user/bonuses``."""

    total_count: int
    limit: int
    data: list[BonusOperationResponse]


class BonusResponseType(BaseDTO):
    """A bonus type reference from ``GET /user/bonuses_types``.

    Here the JSON key is ``type_alias`` and is kept as-is (no aliasing).
    """

    id: int
    type_title: str
    type_alias: str


class RewardResponse(BaseDTO):
    """Paginated bonus history (v2) from ``GET /v2/user/bonuses``.

    The list element is assumed identical to ``BonusOperationResponse`` (the v2
    element is not spelled out in NOVUS_API.md — open question, PLAN.md §9.9).
    """

    data: list[BonusOperationResponse]
    limit: int
    total_count: int
