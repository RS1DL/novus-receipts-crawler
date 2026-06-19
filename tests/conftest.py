"""Shared test infrastructure.

All tests are deterministic: no real network, time or randomness.

- HTTP is mocked via ``httpx.MockTransport`` (helpers ``mock_transport`` /
  ``make_mock_client``).
- Sleeping/backoff is injected via ``fake_sleeper`` (records calls, never
  actually sleeps).
- Input data comes from static JSON fixtures in ``tests/fixtures/`` loaded with
  ``load_fixture``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Type aliases for the injectable test helpers.
RequestHandler = Callable[[httpx.Request], httpx.Response]
FixtureLoader = Callable[[str], Any]
TransportFactory = Callable[[RequestHandler], httpx.MockTransport]


@pytest.fixture
def load_fixture() -> FixtureLoader:
    """Return a loader that parses a JSON fixture from ``tests/fixtures/``."""

    def _load(name: str) -> Any:
        path = FIXTURES_DIR / name
        return json.loads(path.read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def mock_transport() -> TransportFactory:
    """Return a factory building an ``httpx.MockTransport`` from a handler."""

    def _make(handler: RequestHandler) -> httpx.MockTransport:
        return httpx.MockTransport(handler)

    return _make


@pytest.fixture
def make_mock_client(
    mock_transport: TransportFactory,
) -> Callable[..., httpx.Client]:
    """Return a factory for an ``httpx.Client`` backed by ``MockTransport``.

    Either pass an explicit ``handler`` (``httpx.Request -> httpx.Response``)
    for full control, or a canned ``json_data``/``status_code`` for the simple
    "always return this response" case. Extra kwargs go to ``httpx.Client``.
    """

    def _make(
        handler: RequestHandler | None = None,
        *,
        json_data: Any = None,
        status_code: int = 200,
        **client_kwargs: Any,
    ) -> httpx.Client:
        if handler is None:

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(status_code, json=json_data)

        transport = mock_transport(handler)
        return httpx.Client(transport=transport, **client_kwargs)

    return _make


class _FakeSleeper:
    """Records the durations it was asked to sleep, without sleeping."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def fake_sleeper() -> _FakeSleeper:
    """Injectable replacement for ``time.sleep`` that records calls."""

    return _FakeSleeper()
