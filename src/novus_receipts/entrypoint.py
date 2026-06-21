"""Service entrypoint: :class:`PurchaseHistoryJob` (PLAN.md §5).

A planner-agnostic facade: both a cron trigger and a standalone CLI call the
same public :meth:`PurchaseHistoryJob.run`. It wires the layers together --
``AppConfig`` -> :class:`httpx.Client` -> :class:`NovusApiClient` ->
:class:`PurchasesCrawler` -- runs the crawl and returns the raw :class:`CrawlResult`.
Serialisation / persistence of that result is left to the caller (the CLI in
:mod:`novus_receipts.__main__`).

``run()`` always closes the :class:`httpx.Client` it owns (``try/finally``), even
when crawling raises, so connections never leak.
"""

from __future__ import annotations

from typing import Any

import httpx

from novus_receipts.api.client import NovusApiClient
from novus_receipts.config import AppConfig
from novus_receipts.crawler.purchases_crawler import PurchasesCrawler
from novus_receipts.crawler.results import CrawlResult, ReceiptBundle
from novus_receipts.mapping.mappers import Mapper


class PurchaseHistoryJob:
    """Run a full purchase-history crawl for a single account.

    ``config`` defaults to :meth:`AppConfig.from_env` (env + ``.env``). The
    optional ``http_client`` lets a caller (or test) inject a pre-built
    :class:`httpx.Client` -- e.g. one backed by a mock transport; when omitted,
    :meth:`run` builds one from ``config`` with the constant headers and
    timeouts. Either way, :meth:`run` owns and closes the client. The health
    check (``GET /user/profile`` before crawling) is on by default and gated by
    ``with_health_check``.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        http_client: httpx.Client | None = None,
        with_health_check: bool = True,
    ) -> None:
        self._config = config if config is not None else AppConfig.from_env()
        self._http_client = http_client
        self._with_health_check = with_health_check

    @property
    def config(self) -> AppConfig:
        """The resolved configuration (e.g. for the CLI to read ``timezone``)."""

        return self._config

    def run(
        self, *, mapper: Mapper[ReceiptBundle, Any] | None = None
    ) -> CrawlResult[Any]:
        """Build the client, (optionally) health-check, crawl; always close.

        Steps (PLAN.md §5):

        1. build an :class:`httpx.Client` with constant headers + timeouts
           (or use the injected one);
        2. construct :class:`NovusApiClient` and set the starting ``user_token``
           from config;
        3. optionally ``get_profile()`` as a token health-check before crawling;
        4. run :meth:`PurchasesCrawler.crawl_purchase_history` (forwarding the
           optional ``mapper`` -- the DTO -> domain seam, PLAN.md §7);
        5. return the :class:`CrawlResult` (raw bundles, or the mapper's output);
        6. always close the :class:`httpx.Client` (``try/finally``).
        """

        client = self._http_client or self._build_client()
        try:
            api = NovusApiClient(client, self._config)
            # Starting access token (external input) -> sent on auth calls.
            api.set_access_token(self._config.user_token)

            if self._with_health_check:
                api.get_profile()  # fail fast if the token is dead

            crawler = PurchasesCrawler(api, self._config)
            if mapper is None:
                return crawler.crawl_purchase_history()
            return crawler.crawl_purchase_history(mapper=mapper)
        finally:
            client.close()

    def _build_client(self) -> httpx.Client:
        """Build the :class:`httpx.Client` with base URL + constant timeouts.

        The three constant headers (``Platform`` / ``PlatformVersion`` /
        ``private_key``) are pinned by :class:`NovusApiClient`; this sets the
        base URL and the connect/read/write timeouts from config.
        """

        return httpx.Client(
            base_url=self._config.base_url,
            timeout=self._config.timeout_s,
        )
