"""Stable identifiers for stored entities.

A receipt's primary key is a **deterministic** UUID (``uuid5``) derived from its
natural key (``check_number``, ``date``, ``shop_id``) -- not a random ``uuid4``
and not a sequential integer. Determinism matters: re-collecting the same receipt
(or rebuilding the whole database with ``collect --full``) yields the *same* id,
so upserts stay idempotent and any external reference to a receipt (e.g. a future
Obsidian note) keeps a stable key across rebuilds.
"""

from __future__ import annotations

import uuid

# Fixed, arbitrary namespace for novus-receipts ids. Never change it -- doing so
# would reassign every receipt's id.
_RECEIPT_NS = uuid.UUID("6f9d8b2a-1c3e-4f5a-8b7c-0e1d2a3b4c5d")


def receipt_guid(check_number: str, date: int, shop_id: str) -> str:
    """Deterministic receipt id from its natural key, as a UUID string."""

    return str(uuid.uuid5(_RECEIPT_NS, f"{check_number}|{date}|{shop_id}"))
