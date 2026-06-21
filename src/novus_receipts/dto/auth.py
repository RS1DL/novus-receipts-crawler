"""Auth DTOs (PLAN.md §3.2, NOVUS_API.md §2).

``ConfirmWithOtpResponse`` mirrors the response of ``POST /auth/confirm_with_otp``
and is also the response of ``POST /auth/refresh_token`` (both return the same
``ConfirmWithOtpResponseNetModel`` with fresh ``token`` + ``refresh_token``).

The small login-flow DTOs (``CheckNumberResponse`` / ``AuthTokenResponse`` /
``OtpChallengeResponse``) mirror NOVUS_API.md §2 steps 1-3. The OTP-challenge
fields are kept optional because the doc is thin on which keys are always
present; ``BaseDTO`` already ignores any extra keys.

All JSON keys are already snake_case and match the Python field names, so no
``Field(alias=...)`` is needed here.
"""

from __future__ import annotations

from novus_receipts.dto._base import BaseDTO


class CheckNumberResponse(BaseDTO):
    """Reply of ``POST /auth/check_number`` -> whether the phone is registered."""

    user_exists: bool


class AuthTokenResponse(BaseDTO):
    """Reply of ``POST /auth/auth_token`` -> the intermediate ``auth_token``."""

    auth_token: str


class OtpChallengeResponse(BaseDTO):
    """Reply of ``/auth/check_user_by_phone`` and ``/auth/resend_otp``.

    Fields are optional because the decompiled doc only loosely specifies them;
    ``ttl`` is the seconds until an OTP may be resent (NOVUS_API.md §2 step 3).
    """

    message: str | None = None
    ttl: int | None = None
    attempts_left: int | None = None


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
