"""OTP login client + settings + dotenv writer (NOVUS_API.md §2).

This module deliberately avoids :class:`novus_receipts.config.AppConfig`: that
config *requires* ``user_token``, but login is exactly how a token is first
obtained. :class:`LoginSettings` is therefore a minimal ``BaseSettings`` with no
required fields, mirroring ``config.py`` conventions (``NOVUS_`` env prefix,
``DEFAULT_PRIVATE_KEY`` default, ``platform`` as a constant ``ClassVar``).

:class:`LoginClient` mirrors
:class:`novus_receipts.api.client.NovusApiClient`: it pins the three constant
headers on the injected :class:`httpx.Client` and classifies each response with
``errors.raise_for_response`` *before* parsing. None of the login endpoints are
``@Authentication``, so no ``user_token`` header is ever sent. Optional body
fields are omitted entirely when ``None`` (mirroring ``get_bonuses_history``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, TypeVar

import httpx
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from novus_receipts import errors
from novus_receipts.config import DEFAULT_PRIVATE_KEY
from novus_receipts.dto.auth import (
    AuthTokenResponse,
    CheckNumberResponse,
    ConfirmWithOtpResponse,
    OtpChallengeResponse,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


def _without_none(body: Mapping[str, object | None]) -> dict[str, object]:
    """Return ``body`` without the keys whose value is ``None``.

    The Novus auth endpoints expect optional fields (``auth_token``,
    ``google_id``, ``referral_user_id``) to be absent rather than ``null``.
    """

    return {key: value for key, value in body.items() if value is not None}


class LoginSettings(BaseSettings):
    """Minimal settings for the login flow (no required fields, no ``user_token``).

    Loaded from env / ``.env`` with the shared ``NOVUS_`` prefix. ``platform`` is
    a constant ``ClassVar`` (mirroring ``config.py``), so ``NOVUS_PLATFORM``
    cannot override it.
    """

    model_config = SettingsConfigDict(
        env_prefix="NOVUS_",
        env_file=".env",
        extra="ignore",
    )

    base_url: str = "https://api.novus.online"
    private_key: str = DEFAULT_PRIVATE_KEY
    platform: ClassVar[str] = "android"
    platform_version: str = "14 (34)"
    timeout_s: float = 30
    # Device identifier the API requires for the OTP flow. The real app sends a
    # Firebase Installations id; the server accepts any opaque string, so when
    # unset the CLI generates a random one per run (override via NOVUS_GOOGLE_ID
    # to keep a stable "device").
    google_id: str | None = None


class LoginClient:
    """Thin HTTP client over the Novus ``/auth/*`` login endpoints.

    Pins ``Platform`` / ``PlatformVersion`` / ``private_key`` on ``http`` so they
    ride on every request; never sends ``user_token`` (no login endpoint is
    ``@Authentication``).
    """

    def __init__(self, http: httpx.Client, settings: LoginSettings) -> None:
        self._http = http
        self._settings = settings
        http.headers["Platform"] = settings.platform
        http.headers["PlatformVersion"] = settings.platform_version
        http.headers["private_key"] = settings.private_key

    # --- internals ----------------------------------------------------------

    def _post(
        self,
        model: type[ModelT],
        path: str,
        json: dict[str, object],
    ) -> ModelT:
        """POST ``json`` to ``path``, classify the response, then parse it.

        ``errors.raise_for_response`` runs *before* ``model_validate`` so an
        error status surfaces as the right typed exception, not a validation
        failure on an error body.
        """

        response = self._http.post(path, json=json)
        errors.raise_for_response(response)
        return model.model_validate(response.json())

    # --- login flow (NOVUS_API.md §2) ---------------------------------------

    def check_number(self, phone: str) -> CheckNumberResponse:
        """POST ``/auth/check_number`` -> whether the phone is registered."""

        return self._post(
            CheckNumberResponse,
            "/auth/check_number",
            {"phone": phone},
        )

    def create_auth_token(self, google_id: str, phone: str) -> AuthTokenResponse:
        """POST ``/auth/auth_token`` -> an intermediate ``auth_token``.

        NOVUS_API.md §2 step 2 (optional): the returned ``auth_token`` can then
        be threaded into :meth:`request_otp` / :meth:`confirm_otp`.
        """

        return self._post(
            AuthTokenResponse,
            "/auth/auth_token",
            {"google_id": google_id, "phone": phone},
        )

    def _send_otp_request(
        self,
        path: str,
        phone: str,
        auth_token: str | None,
        google_id: str | None,
    ) -> OtpChallengeResponse:
        """Shared body for ``check_user_by_phone`` / ``resend_otp`` (same shape)."""

        return self._post(
            OtpChallengeResponse,
            path,
            _without_none(
                {"phone": phone, "auth_token": auth_token, "google_id": google_id}
            ),
        )

    def request_otp(
        self,
        phone: str,
        *,
        auth_token: str | None = None,
        google_id: str | None = None,
    ) -> OtpChallengeResponse:
        """POST ``/auth/check_user_by_phone`` -> sends the SMS code.

        ``auth_token`` / ``google_id`` are omitted from the body when ``None``.
        """

        return self._send_otp_request(
            "/auth/check_user_by_phone", phone, auth_token, google_id
        )

    def resend_otp(
        self,
        phone: str,
        *,
        auth_token: str | None = None,
        google_id: str | None = None,
    ) -> OtpChallengeResponse:
        """POST ``/auth/resend_otp`` -> resends the SMS code (same body/reply)."""

        return self._send_otp_request(
            "/auth/resend_otp", phone, auth_token, google_id
        )

    def confirm_otp(
        self,
        otp: str,
        phone: str,
        *,
        referral_user_id: str | None = None,
        auth_token: str | None = None,
        google_id: str | None = None,
    ) -> ConfirmWithOtpResponse:
        """POST ``/auth/confirm_with_otp`` -> the session (``token`` + refresh).

        ``referral_user_id`` / ``auth_token`` / ``google_id`` are omitted from
        the body when ``None``.
        """

        return self._post(
            ConfirmWithOtpResponse,
            "/auth/confirm_with_otp",
            _without_none(
                {
                    "otp": otp,
                    "phone": phone,
                    "referral_user_id": referral_user_id,
                    "auth_token": auth_token,
                    "google_id": google_id,
                }
            ),
        )


def normalize_phone(raw: str) -> str:
    """Normalise a Ukrainian phone to the API's ``380XXXXXXXXX`` form.

    The Novus API rejects a leading ``+`` and any spacing/punctuation with
    ``{"message": "Invalid parameters passed: phone"}``; it expects the country
    code ``380`` followed by the 9-digit subscriber number. This accepts the
    common shapes -- ``+380…``, ``380…``, ``00380…``, national ``0XXXXXXXXX``
    and the bare 9 digits -- and maps them all to ``380XXXXXXXXX``.
    """

    digits = re.sub(r"\D", "", raw)  # drop '+', spaces, dashes, parentheses
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("380"):
        return digits
    if digits.startswith("0"):
        return "380" + digits[1:]
    if len(digits) == 9:
        return "380" + digits
    return digits


def write_tokens_to_env(
    env_path: str | Path,
    *,
    user_token: str,
    refresh_token: str,
) -> None:
    """Upsert ``NOVUS_USER_TOKEN`` / ``NOVUS_REFRESH_TOKEN`` into a dotenv file.

    Preserves every other line and comment, replaces the two keys in place if
    already present (no duplicates), creates the file if missing, and is
    idempotent on re-run. Line-based and simple: values are written raw.
    """

    path = Path(env_path)
    updates = {
        "NOVUS_USER_TOKEN": user_token,
        "NOVUS_REFRESH_TOKEN": refresh_token,
    }

    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    # Splitting on "\n" keeps blank lines and comments intact; a trailing
    # newline yields a trailing "" element we drop before re-joining.
    lines = existing.split("\n")
    had_trailing_newline = existing.endswith("\n")
    if had_trailing_newline:
        lines = lines[:-1]

    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else None
        if key in updates:
            if key in seen:
                continue  # drop any stale pre-existing duplicate of a managed key
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)

    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")

    path.write_text("\n".join(out) + "\n", encoding="utf-8")
