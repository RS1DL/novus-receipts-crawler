"""HTTP API layer: the thin :class:`NovusApiClient` wrapper.

One public method maps to exactly one Novus endpoint (PLAN.md §2): it serialises
parameters, performs the request, classifies the response into typed errors and
deserialises the body into a DTO. No pagination, retries, refresh logic or
stitching live here -- that is the crawler's job.
"""

from __future__ import annotations

from novus_receipts.api.client import NovusApiClient

__all__ = ["NovusApiClient"]
