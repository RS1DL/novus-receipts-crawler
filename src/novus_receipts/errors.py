"""Service exception hierarchy and HTTP/transport classification (PLAN.md §8).

The crawler must work only in terms of these typed exceptions, never raw
status codes, so it can tell apart auth failures (trigger a refresh),
rate limiting, retriable transient failures and fatal API errors.

Status semantics (NOVUS_API.md is silent on the exact failure trigger, so the
documented assumption from PLAN.md §9 is applied):

- 2xx               -> success, no exception
- 401 / 403         -> :class:`NovusAuthError`   (token invalid/expired)
- 429               -> :class:`NovusRateLimitError` (optional ``Retry-After``)
- 5xx               -> :class:`NovusTransientError` (retriable)
- other 4xx         -> :class:`NovusApiError` (fatal, carries status + body)
- transport errors  -> :class:`NovusTransientError`
"""

from __future__ import annotations

from collections.abc import Callable

import httpx


class NovusError(Exception):
    """Base class for all errors raised by the Novus receipts service."""


class NovusAuthError(NovusError):
    """Token is invalid or expired (HTTP 401/403); triggers a refresh."""


class NovusRateLimitError(NovusError):
    """Rate limited by the server (HTTP 429).

    ``retry_after`` holds the parsed ``Retry-After`` header in seconds when the
    server provided one, otherwise ``None``.
    """

    def __init__(self, *args: object, retry_after: float | None = None) -> None:
        super().__init__(*args)
        self.retry_after = retry_after


class NovusTransientError(NovusError):
    """Retriable failure: network/timeout/transport error or HTTP 5xx."""


class NovusApiError(NovusError):
    """Fatal, non-retriable API error (unexpected 4xx).

    Carries the HTTP ``status`` and raw response ``body`` for diagnostics.
    """

    def __init__(self, *, status: int, body: str) -> None:
        super().__init__(f"Novus API error {status}: {body}")
        self.status = status
        self.body = body


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Parse the ``Retry-After`` header into seconds, or ``None`` if absent/invalid."""

    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _auth(response: httpx.Response) -> NovusError:
    return NovusAuthError(f"authentication failed (HTTP {response.status_code})")


def _rate_limit(response: httpx.Response) -> NovusError:
    return NovusRateLimitError(
        "rate limited (HTTP 429)", retry_after=_parse_retry_after(response)
    )


# Exact-status overrides; ranges (5xx / other 4xx) are handled afterwards.
_STATUS_FACTORIES: dict[int, Callable[[httpx.Response], NovusError]] = {
    401: _auth,
    403: _auth,
    429: _rate_limit,
}


def raise_for_response(response: httpx.Response) -> None:
    """Raise the typed exception matching ``response``'s status, or return on 2xx."""

    status = response.status_code
    if 200 <= status < 300:
        return

    factory = _STATUS_FACTORIES.get(status)
    if factory is not None:
        raise factory(response)

    if 500 <= status < 600:
        raise NovusTransientError(f"server error (HTTP {status})")

    raise NovusApiError(status=status, body=response.text)


def classify_transport_error(exc: httpx.TransportError) -> NovusTransientError:
    """Wrap a transport error in :class:`NovusTransientError` for the caller to raise."""

    wrapped = NovusTransientError(f"transport error: {exc}")
    wrapped.__cause__ = exc
    return wrapped
