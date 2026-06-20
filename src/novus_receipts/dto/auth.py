"""Auth DTOs (PLAN.md §3.2, NOVUS_API.md §2 step 4).

``ConfirmWithOtpResponse`` mirrors the response of ``POST /auth/confirm_with_otp``
and is also the response of ``POST /auth/refresh_token`` (both return the same
``ConfirmWithOtpResponseNetModel`` with fresh ``token`` + ``refresh_token``).

All JSON keys are already snake_case and match the Python field names, so no
``Field(alias=...)`` is needed here.
"""

from __future__ import annotations

from novus_receipts.dto._base import BaseDTO


class ConfirmWithOtpResponse(BaseDTO):
    """Session payload returned by ``/auth/confirm_with_otp`` and
    ``/auth/refresh_token``."""

    token: str
    refresh_token: str
    first_authorization: bool
    user_first_name: str
    bonuses: str
    bonus_type: str
    blocked_card: bool
