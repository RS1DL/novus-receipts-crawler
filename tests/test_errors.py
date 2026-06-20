"""T2.1 / T2.2: error hierarchy and response/transport classification.

Covers PLAN.md §8 and the status semantics from NOVUS_API.md:

- ``NovusError`` base; subclasses ``NovusAuthError`` (401/403),
  ``NovusRateLimitError`` (429, optional ``retry_after``),
  ``NovusTransientError`` (5xx / transport), ``NovusApiError`` (other 4xx,
  carries ``status`` + ``body``).
- ``raise_for_response`` maps an ``httpx.Response`` status to the right
  exception (or returns ``None`` for 2xx).
- ``classify_transport_error`` wraps an ``httpx.TransportError`` into a
  ``NovusTransientError`` for the caller to raise.
"""

from __future__ import annotations

import httpx
import pytest

from novus_receipts.errors import (
    NovusApiError,
    NovusAuthError,
    NovusError,
    NovusRateLimitError,
    NovusTransientError,
    classify_transport_error,
    raise_for_response,
)


def _response(
    status_code: int,
    *,
    headers: dict[str, str] | None = None,
    text: str = "",
) -> httpx.Response:
    """Build a standalone ``httpx.Response`` with the given status/headers/body."""

    return httpx.Response(status_code, headers=headers, text=text)


# --- T2.1: hierarchy ------------------------------------------------------


def test_base_is_exception() -> None:
    assert issubclass(NovusError, Exception)


@pytest.mark.parametrize(
    "subclass",
    [NovusAuthError, NovusRateLimitError, NovusTransientError, NovusApiError],
)
def test_subclasses_inherit_from_base(subclass: type[NovusError]) -> None:
    assert issubclass(subclass, NovusError)


def test_subclasses_are_distinct() -> None:
    assert not issubclass(NovusAuthError, NovusRateLimitError)
    assert not issubclass(NovusTransientError, NovusApiError)
    assert not issubclass(NovusApiError, NovusTransientError)


def test_rate_limit_default_retry_after_is_none() -> None:
    exc = NovusRateLimitError("slow down")
    assert exc.retry_after is None


def test_rate_limit_stores_retry_after() -> None:
    exc = NovusRateLimitError("slow down", retry_after=2.5)
    assert exc.retry_after == 2.5


def test_api_error_stores_status_and_body() -> None:
    exc = NovusApiError(status=404, body='{"code": 1, "message": "nope"}')
    assert exc.status == 404
    assert exc.body == '{"code": 1, "message": "nope"}'


def test_api_error_can_be_raised_and_caught_as_base() -> None:
    with pytest.raises(NovusError):
        raise NovusApiError(status=400, body="bad")


# --- T2.2: raise_for_response ---------------------------------------------


@pytest.mark.parametrize("status_code", [200, 201, 204, 299])
def test_2xx_returns_none(status_code: int) -> None:
    assert raise_for_response(_response(status_code)) is None


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_statuses_raise_auth_error(status_code: int) -> None:
    with pytest.raises(NovusAuthError):
        raise_for_response(_response(status_code))


def test_429_without_retry_after_header() -> None:
    with pytest.raises(NovusRateLimitError) as info:
        raise_for_response(_response(429))
    assert info.value.retry_after is None


def test_429_with_retry_after_header_parsed_to_float() -> None:
    with pytest.raises(NovusRateLimitError) as info:
        raise_for_response(_response(429, headers={"Retry-After": "7"}))
    assert info.value.retry_after == 7.0
    assert isinstance(info.value.retry_after, float)


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_5xx_raise_transient_error(status_code: int) -> None:
    with pytest.raises(NovusTransientError):
        raise_for_response(_response(status_code))


@pytest.mark.parametrize("status_code", [400, 404, 409, 422])
def test_other_4xx_raise_api_error_with_status_and_body(status_code: int) -> None:
    body = f"error body for {status_code}"
    with pytest.raises(NovusApiError) as info:
        raise_for_response(_response(status_code, text=body))
    assert info.value.status == status_code
    assert info.value.body == body


def test_raise_for_response_works_with_mock_client(make_mock_client) -> None:
    client = make_mock_client(json_data={"detail": "boom"}, status_code=500)
    response = client.get("https://example.test/anything")
    with pytest.raises(NovusTransientError):
        raise_for_response(response)


def test_raise_for_response_404_via_mock_client(make_mock_client) -> None:
    client = make_mock_client(json_data={"code": 1}, status_code=404)
    response = client.get("https://example.test/missing")
    with pytest.raises(NovusApiError) as info:
        raise_for_response(response)
    assert info.value.status == 404


def test_raise_for_response_200_via_mock_client(make_mock_client) -> None:
    client = make_mock_client(json_data={"ok": True}, status_code=200)
    response = client.get("https://example.test/ok")
    assert raise_for_response(response) is None


# --- T2.2: classify_transport_error ---------------------------------------


def test_classify_transport_error_returns_transient() -> None:
    exc = httpx.ConnectTimeout("timed out")
    result = classify_transport_error(exc)
    assert isinstance(result, NovusTransientError)


def test_classify_transport_error_chains_cause() -> None:
    exc = httpx.ConnectError("refused")
    result = classify_transport_error(exc)
    assert result.__cause__ is exc


def test_classify_transport_error_can_be_raised() -> None:
    exc = httpx.ReadTimeout("slow")
    with pytest.raises(NovusTransientError):
        raise classify_transport_error(exc)
