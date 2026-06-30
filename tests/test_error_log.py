"""Sidecar crawl-error log (``store.error_log``).

Crawl errors are operational data and live OUTSIDE the receipts DB, as JSONL.
"""

from __future__ import annotations

import json
from pathlib import Path

from novus_receipts.crawler.results import CrawlItemError
from novus_receipts.dto.purchases import PurchaseResponse
from novus_receipts.store.error_log import CrawlErrorLog, default_error_log_path


def _check() -> PurchaseResponse:
    return PurchaseResponse.model_validate(
        {
            "shop_id": "1",
            "amount": "1.00",
            "bonus": "0",
            "check_number": "bad",
            "date": 1_718_000_000,
            "shop_address": "Kyiv",
        }
    )


def test_default_error_log_path_sits_beside_the_db() -> None:
    assert default_error_log_path("/tmp/x.db") == "/tmp/x.db.errors.jsonl"


def test_record_appends_one_json_line_per_error(tmp_path: Path) -> None:
    path = tmp_path / "e.jsonl"
    log = CrawlErrorLog(path)
    err = CrawlItemError(check=_check(), error=ValueError("boom"))

    log.record(err, now_ts=111)
    log.record(err, now_ts=222)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["check_number"] == "bad"
    assert rec["shop_id"] == "1"
    assert rec["error_type"] == "ValueError"
    assert "boom" in rec["error_text"]
    assert rec["occurred_ts"] == 111
