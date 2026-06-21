"""``PurchasesCrawler`` -- the orchestration layer (PLAN.md §4).

The crawler owns everything above the thin API client: it walks the purchase
list pagination, flattens month groups, stitches each summary to its
detalization, manages the session token, retries transient failures with
exponential backoff and reacts to auth failures with a single refresh. It knows
nothing about HTTP -- it only calls ``NovusApiClient`` methods and reasons about
the typed exceptions from :mod:`novus_receipts.errors`.

Resilience (PLAN.md §4.5):

- :class:`~novus_receipts.errors.NovusTransientError` -> exponential-backoff
  retries via ``tenacity`` using the **injected** sleeper, up to
  ``config.max_retries`` attempts, then re-raise.
- :class:`~novus_receipts.errors.NovusRateLimitError` -> retried on the same
  budget, waiting for ``Retry-After`` when the server supplied one (otherwise
  exponential backoff).
- :class:`~novus_receipts.errors.NovusAuthError` -> refresh the session exactly
  once under a lock, retry the call once; a second auth error is fatal.

The sleeper is injected (``sleeper=time.sleep`` by default) so tests can supply
a fake that records durations without sleeping.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, overload

from pydantic import ValidationError
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from novus_receipts.config import AppConfig
from novus_receipts.crawler.pagination import iter_purchase_pages
from novus_receipts.crawler.results import (
    CrawlItemError,
    CrawlResult,
    ReceiptBundle,
    ReceiptDetail,
)
from novus_receipts.dto.purchases import Purchase2Response, PurchaseResponse
from novus_receipts.errors import (
    NovusAuthError,
    NovusError,
    NovusRateLimitError,
    NovusTransientError,
)
from novus_receipts.mapping.mappers import IdentityMapper, Mapper

if TYPE_CHECKING:
    from novus_receipts.dto.auth import ConfirmWithOtpResponse
    from novus_receipts.dto.bill import BillResponse, PurchaseDetalizationResponse
    from novus_receipts.dto.bonuses import UserBonusResponse

T = TypeVar("T")
_R = TypeVar("_R")  # mapper output type for crawl_purchase_history


class _Api(Protocol):
    """The subset of ``NovusApiClient`` the crawler depends on.

    Declared structurally so the crawler is decoupled from the concrete client
    (and so tests can pass a lightweight fake double).
    """

    @property
    def access_token(self) -> str | None: ...

    def set_access_token(self, token: str) -> None: ...

    def refresh_token(self, refresh_token: str) -> ConfirmWithOtpResponse: ...

    def get_purchases_2(self, page: int = 1) -> Purchase2Response: ...

    def get_bill(
        self, store: str, date: int, check_number: str, work_station_id: str
    ) -> BillResponse: ...

    def get_purchase(
        self, store: str, date: int, check_number: str
    ) -> PurchaseDetalizationResponse: ...

    def get_current_bonuses(self) -> UserBonusResponse: ...


class PurchasesCrawler:
    """Crawl a single account's purchase history, details and bonus balance."""

    def __init__(
        self,
        api: _Api,
        config: AppConfig,
        *,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api = api
        self._config = config
        self._sleep = sleeper
        self._refresh_lock = threading.Lock()

    # -- public orchestration ----------------------------------------------

    @overload
    def crawl_purchase_history(
        self,
        *,
        with_details: bool = ...,
        with_bonuses: bool = ...,
        max_pages: int | None = ...,
        mapper: None = ...,
    ) -> CrawlResult[ReceiptBundle]: ...

    @overload
    def crawl_purchase_history(
        self,
        *,
        with_details: bool = ...,
        with_bonuses: bool = ...,
        max_pages: int | None = ...,
        mapper: Mapper[ReceiptBundle, _R],
    ) -> CrawlResult[_R]: ...

    def crawl_purchase_history(
        self,
        *,
        with_details: bool = True,
        with_bonuses: bool = True,
        max_pages: int | None = None,
        mapper: Mapper[ReceiptBundle, Any] | None = None,
    ) -> CrawlResult[Any]:
        """Walk the history, stitch details and (optionally) read the balance.

        Pagination is flattened into ordered :class:`ReceiptBundle` items. A
        detail fetch that fails for a single check is non-fatal: that bundle's
        ``detail`` is ``None`` and a :class:`CrawlItemError` is recorded while
        the rest continue. ``with_details`` / ``with_bonuses`` gate the detail
        and balance steps; ``max_pages`` caps how many pages are pulled.

        ``mapper`` is the isolated DTO -> domain extension point (PLAN.md §7).
        Each stitched :class:`ReceiptBundle` is passed through ``mapper.map``
        before it lands in :attr:`CrawlResult.receipts`. ``None`` means identity
        (an :class:`IdentityMapper`), so the raw DTO bundles are returned
        unchanged; a custom mapper transforms each bundle in place of identity.
        """

        bundle_mapper: Mapper[ReceiptBundle, Any] = (
            mapper if mapper is not None else IdentityMapper()
        )

        receipts: list[Any] = []
        item_errors: list[CrawlItemError] = []
        pages_fetched = 0
        total_count: int | None = None

        for page_resp in self._iter_purchase_pages():
            total_count = page_resp.total_count
            pages_fetched += 1

            for check in self._flatten_checks(page_resp):
                detail = self._collect_detail(check, with_details, item_errors)
                bundle = ReceiptBundle(summary=check, detail=detail)
                receipts.append(bundle_mapper.map(bundle))
                if with_details:
                    self._sleep(self._config.request_delay_s)

            if max_pages is not None and pages_fetched >= max_pages:
                break

        bonuses: UserBonusResponse | None = None
        if with_bonuses:
            bonuses = self._call_with_resilience(self._api.get_current_bonuses)

        return CrawlResult(
            receipts=receipts,
            current_bonuses=bonuses,
            pages_fetched=pages_fetched,
            total_count=total_count,
            errors=item_errors,
        )

    # -- internals ----------------------------------------------------------

    def _collect_detail(
        self,
        check: PurchaseResponse,
        with_details: bool,
        item_errors: list[CrawlItemError],
    ) -> ReceiptDetail:
        """Fetch one check's detail, capturing a non-fatal failure as an error."""

        if not with_details:
            return None
        try:
            return self._call_with_resilience(lambda: self._fetch_detail(check))
        except (NovusError, ValidationError) as exc:  # non-fatal: record, carry on
            # A NovusError (API/transport) OR a pydantic ValidationError (a single
            # receipt whose detail shape we can't parse) must not abort the whole
            # crawl -- record it and keep the rest of the receipts.
            item_errors.append(CrawlItemError(check=check, error=exc))
            return None

    def _iter_purchase_pages(self) -> Iterator[Purchase2Response]:
        """Paginate ``/user/purchases_2`` through the resilient fetch layer."""

        return iter_purchase_pages(
            lambda page: self._call_with_resilience(
                lambda: self._api.get_purchases_2(page=page)
            )
        )

    @staticmethod
    def _flatten_checks(page: Purchase2Response) -> list[PurchaseResponse]:
        """Flatten a page's month groups into a flat check list, order preserved."""

        return [check for month in page.data for check in month.data]

    def _fetch_detail(self, check: PurchaseResponse) -> ReceiptDetail:
        """Fetch a receipt's detalization, falling back when no work station id.

        Primary path is the richer ``get_bill`` (``/v2/user/purchase``); when the
        check has no ``work_station_id`` it falls back to ``get_purchase``
        (``/user/purchase_2``), which does not need it.
        """

        store = check.shop_id
        if check.work_station_id:
            return self._api.get_bill(
                store=store,
                date=check.date,
                check_number=check.check_number,
                work_station_id=check.work_station_id,
            )
        return self._api.get_purchase(
            store=store, date=check.date, check_number=check.check_number
        )

    def _call_with_resilience(self, fn: Callable[[], T]) -> T:
        """Run ``fn`` with transient retries and a single reactive refresh.

        Transient failures are retried with exponential backoff (injected
        sleeper). An auth failure triggers one refresh under a lock, then a
        single retry; a second auth failure propagates.
        """

        token_before = self._api.access_token
        try:
            return self._retry_transient(fn)
        except NovusAuthError:
            self._refresh_under_lock(token_before)
            return self._retry_transient(fn)

    def _retry_transient(self, fn: Callable[[], T]) -> T:
        """Retry ``fn`` on transient and rate-limit errors with backoff.

        Transient errors use exponential backoff; a rate-limit error waits for
        its ``Retry-After`` when the server supplied one, otherwise it falls
        back to the same exponential backoff (PLAN.md §4.5 steps 3-4). Every
        wait goes through the injected sleeper and the whole thing is bounded by
        ``config.max_retries`` attempts before re-raising.
        """

        exponential = wait_exponential(multiplier=self._config.backoff_base_s)

        def wait(retry_state: RetryCallState) -> float:
            exc = (
                retry_state.outcome.exception()
                if retry_state.outcome is not None
                else None
            )
            if isinstance(exc, NovusRateLimitError) and exc.retry_after is not None:
                return exc.retry_after
            return exponential(retry_state)

        retryer = Retrying(
            sleep=self._sleep,
            stop=stop_after_attempt(self._config.max_retries),
            wait=wait,
            retry=retry_if_exception_type((NovusTransientError, NovusRateLimitError)),
            reraise=True,
        )
        return retryer(fn)

    def _refresh_under_lock(self, token_before: str | None) -> None:
        """Refresh the session exactly once even under concurrent auth failures.

        The lock serialises contending callers; the token check skips the
        refresh for any caller whose token was already rotated by another thread
        while it waited, so a burst of auth failures refreshes only once.
        """

        with self._refresh_lock:
            if self._api.access_token != token_before:
                return  # another thread already refreshed
            self._refresh_session()

    def _refresh_session(self) -> None:
        """Reactively refresh the session token (PLAN.md §4.5).

        Requires only ``config.refresh_token`` (OTP login is out of scope -- the
        token is an external input). Without one, refresh is impossible.
        """

        refresh = self._config.refresh_token
        if not refresh:
            raise NovusAuthError("no refresh token; provide a fresh user_token")
        resp = self._api.refresh_token(refresh)
        self._api.set_access_token(resp.token)
        self._config.update_refresh_token(resp.refresh_token)
