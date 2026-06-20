"""Crawler package: purchase-history orchestration (PLAN.md §4).

Re-exports the crawler, its pagination helper and the result containers so
callers can ``from novus_receipts.crawler import PurchasesCrawler, CrawlResult``.
"""

from __future__ import annotations

from novus_receipts.crawler.pagination import iter_purchase_pages
from novus_receipts.crawler.purchases_crawler import PurchasesCrawler
from novus_receipts.crawler.results import (
    CrawlItemError,
    CrawlResult,
    ReceiptBundle,
    ReceiptDetail,
)

__all__ = [
    "CrawlItemError",
    "CrawlResult",
    "PurchasesCrawler",
    "ReceiptBundle",
    "ReceiptDetail",
    "iter_purchase_pages",
]
