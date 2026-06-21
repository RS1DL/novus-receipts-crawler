"""``NovusApiClient`` -- a thin, one-method-per-endpoint wrapper (PLAN.md §2).

Each public method maps to exactly one Novus endpoint: it builds the query/JSON,
issues the request via the injected :class:`httpx.Client`, classifies the
response into a typed error (``errors.raise_for_response``) *before* parsing, and
deserialises the body into the matching DTO. There is no pagination, retry,
refresh or stitching here -- the crawler owns that and only sees the typed
exceptions from :mod:`novus_receipts.errors`.

Headers (NOVUS_API.md §1.2-1.3):

- ``Platform`` / ``PlatformVersion`` / ``private_key`` are constant and set once
  on the underlying :class:`httpx.Client`, so every request carries them.
- ``user_token`` is added per-call only on ``@Authentication`` methods.
"""

from __future__ import annotations

from typing import TypeVar

import httpx
from pydantic import BaseModel

from novus_receipts import errors
from novus_receipts.config import AppConfig
from novus_receipts.dto.auth import ConfirmWithOtpResponse
from novus_receipts.dto.bill import (
    BillResponse,
    ProfileResponse,
    PurchaseDetalizationResponse,
)
from novus_receipts.dto.bonuses import (
    BonusesResponse,
    BonusResponseType,
    UserBonusResponse,
)
from novus_receipts.dto.envelope import Data
from novus_receipts.dto.purchases import (
    Purchase2Response,
    PurchaseDetailsResponse,
    ShoppingDetailsResponse,
)

ModelT = TypeVar("ModelT", bound=BaseModel)

# Header names (NOVUS_API.md §1.2-1.3).
_USER_TOKEN_HEADER = "user_token"


class NovusApiClient:
    """Thin HTTP client over the Novus API; one public method per endpoint."""

    def __init__(self, http: httpx.Client, config: AppConfig) -> None:
        """Wire up the client and pin the constant headers on ``http``.

        ``http`` is an :class:`httpx.Client` whose ``base_url`` and timeouts are
        configured by the caller; this constructor adds the three constant
        headers so they ride on every request.
        """

        self._http = http
        self._config = config
        self._access_token: str | None = config.user_token
        http.headers["Platform"] = config.platform
        http.headers["PlatformVersion"] = config.platform_version
        http.headers["private_key"] = config.private_key

    @property
    def access_token(self) -> str | None:
        """The in-memory ``user_token`` sent on authenticated calls."""

        return self._access_token

    def set_access_token(self, token: str) -> None:
        """Replace the in-memory ``user_token`` (e.g. after a reactive refresh)."""

        self._access_token = token

    # --- internals ----------------------------------------------------------

    def _auth_headers(self, auth: bool) -> dict[str, str] | None:
        """Per-call ``user_token`` header for ``@Authentication`` methods only."""

        if not auth or self._access_token is None:
            return None
        return {_USER_TOKEN_HEADER: self._access_token}

    def _request(
        self,
        model: type[ModelT],
        method: str,
        path: str,
        *,
        auth: bool,
        params: dict[str, int | str] | None = None,
        json: dict[str, object] | None = None,
    ) -> ModelT:
        """Issue a request, classify the response, then parse it into ``model``.

        ``errors.raise_for_response`` runs *before* ``model_validate`` so that an
        error status (e.g. 401/500) surfaces as the right typed exception rather
        than a validation failure on an error body.
        """

        response = self._http.request(
            method,
            path,
            params=params,
            json=json,
            headers=self._auth_headers(auth),
        )
        errors.raise_for_response(response)
        return model.model_validate(response.json())

    def _get(
        self,
        model: type[ModelT],
        path: str,
        *,
        params: dict[str, int | str] | None = None,
        auth: bool = True,
    ) -> ModelT:
        """GET ``path`` -> ``model``. Authenticated (``user_token``) by default."""

        return self._request(model, "GET", path, auth=auth, params=params)

    def _post(
        self,
        model: type[ModelT],
        path: str,
        *,
        json: dict[str, object] | None = None,
        auth: bool = True,
    ) -> ModelT:
        """POST ``json`` to ``path`` -> ``model``. Authenticated by default."""

        return self._request(model, "POST", path, auth=auth, json=json)

    # --- authorization (for reactive refresh) -------------------------------

    def refresh_token(self, refresh_token: str) -> ConfirmWithOtpResponse:
        """POST ``/auth/refresh_token`` (no ``user_token``) -> fresh session."""

        return self._post(
            ConfirmWithOtpResponse,
            "/auth/refresh_token",
            json={"refresh_token": refresh_token},
            auth=False,
        )

    # --- purchase history (receipt lists) -----------------------------------

    def get_purchases(self, page: int = 1) -> PurchaseDetailsResponse:
        """GET ``/user/purchases?page=`` (variant A)."""

        return self._get(
            PurchaseDetailsResponse, "/user/purchases", params={"page": page}
        )

    def get_purchases_2(
        self, page: int = 1, limit: int | None = None
    ) -> Purchase2Response:
        """GET ``/user/purchases_2?page=`` (variant B).

        ``limit`` is not in the app's own request signature, but the server
        honours it (the default page size is 10); sending a larger value lets the
        crawler pull more receipts per request. Omitted from the query when
        ``None`` (server default applies).
        """

        params: dict[str, int | str] = {"page": page}
        if limit is not None:
            params["limit"] = limit
        return self._get(Purchase2Response, "/user/purchases_2", params=params)

    def get_shopping(self, page: int = 1, limit: int = 10) -> ShoppingDetailsResponse:
        """GET ``/v2/user/purchases?page=&limit=`` (variant C)."""

        return self._get(
            ShoppingDetailsResponse,
            "/v2/user/purchases",
            params={"page": page, "limit": limit},
        )

    # --- receipt detalization -----------------------------------------------

    def get_purchase(
        self, store: str, date: int, check_number: str
    ) -> PurchaseDetalizationResponse:
        """GET ``/user/purchase_2?store=&date=&check_number=`` (variant A)."""

        return self._get(
            PurchaseDetalizationResponse,
            "/user/purchase_2",
            params={"store": store, "date": date, "check_number": check_number},
        )

    def get_bill(
        self,
        store: str,
        date: int,
        check_number: str,
        work_station_id: str,
    ) -> BillResponse:
        """GET ``/v2/user/purchase?store=&date=&check_number=&work_station_id=``."""

        return self._get(
            BillResponse,
            "/v2/user/purchase",
            params={
                "store": store,
                "date": date,
                "check_number": check_number,
                "work_station_id": work_station_id,
            },
        )

    # --- bonuses ------------------------------------------------------------

    def get_current_bonuses(self) -> UserBonusResponse:
        """GET ``/user/bonuses/current`` -> the current bonus balance."""

        return self._get(UserBonusResponse, "/user/bonuses/current")

    def get_bonuses_history(
        self,
        page: int = 1,
        limit: int = 10,
        type_id: int | None = None,
    ) -> BonusesResponse:
        """GET ``/user/bonuses?page=&limit=`` (+ optional ``type_id``).

        ``type_id`` is omitted from the query string entirely when ``None``.
        """

        params: dict[str, int | str] = {"page": page, "limit": limit}
        if type_id is not None:
            params["type_id"] = type_id
        return self._get(BonusesResponse, "/user/bonuses", params=params)

    def get_bonuses_types(self) -> Data[list[BonusResponseType]]:
        """GET ``/user/bonuses_types`` (no ``user_token``) -> bonus type refs."""

        return self._get(
            Data[list[BonusResponseType]], "/user/bonuses_types", auth=False
        )

    # --- profile (token health-check) ---------------------------------------

    def get_profile(self) -> ProfileResponse:
        """GET ``/user/profile`` -> the user profile (token health-check)."""

        return self._get(ProfileResponse, "/user/profile")
