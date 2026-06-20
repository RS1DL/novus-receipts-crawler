"""Reusable purchase-history pagination (PLAN.md §4.4, TASKS.md T5.2).

``/user/purchases_2`` exposes ``total_count``/``page`` but no ``limit`` to set the
page size, so the stop condition relies on two signals (PLAN.md §9.5):

1. an **empty page** (no checks across any month group) -> end of history;
2. ``seen >= total_count`` -> everything has been pulled.

The generator is decoupled from the API: it takes a ``fetch_page`` callable
``(page: int) -> Purchase2Response`` so it stays reusable and trivially testable
(the crawler injects one that wraps ``api.get_purchases_2`` in its resilience
layer).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from novus_receipts.dto.purchases import Purchase2Response

#: A page-fetch callable: given a 1-based page index, return that page.
PageFetcher = Callable[[int], Purchase2Response]


def _count_checks(page: Purchase2Response) -> int:
    """Total number of checks across every month group on ``page``."""

    return sum(len(month.data) for month in page.data)


def iter_purchase_pages(fetch_page: PageFetcher) -> Iterator[Purchase2Response]:
    """Yield purchase pages 1, 2, 3, ... until an empty page or ``total_count``.

    A page with zero checks stops the walk *before* being yielded. After a
    non-empty page is yielded, the walk stops once the running ``seen`` count
    reaches the page's ``total_count``.
    """

    page = 1
    seen = 0
    while True:
        resp = fetch_page(page)
        checks_on_page = _count_checks(resp)
        if checks_on_page == 0:
            return
        yield resp
        seen += checks_on_page
        if resp.total_count and seen >= resp.total_count:
            return
        page += 1
