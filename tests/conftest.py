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
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

#: Constants shared across HTTP-client tests.
BASE_URL = "https://api.novus.online"
PRIVATE_KEY = "070696aa5d8844e3c71e90604a7b0a11dc0c99638d3f2e0bf53c2f091ac52d4c"

#: Every NOVUS_* env var AppConfig / LoginSettings read. Cleared before each test
#: (see ``_clear_novus_env``) so the host shell never leaks into a test.
NOVUS_ENV_VARS = (
    "NOVUS_BASE_URL",
    "NOVUS_USER_TOKEN",
    "NOVUS_REFRESH_TOKEN",
    "NOVUS_PRIVATE_KEY",
    "NOVUS_PLATFORM",
    "NOVUS_PLATFORM_VERSION",
    "NOVUS_TIMEOUT_S",
    "NOVUS_MAX_RETRIES",
    "NOVUS_BACKOFF_BASE_S",
    "NOVUS_REQUEST_DELAY_S",
    "NOVUS_DETAIL_CONCURRENCY",
    "NOVUS_TIMEZONE",
    "NOVUS_PAGE_SIZE",
    "NOVUS_DB_PATH",
    "NOVUS_COLLECT_OVERLAP_S",
)

# Type aliases for the injectable test helpers.
RequestHandler = Callable[[httpx.Request], httpx.Response]
FixtureLoader = Callable[[str], Any]
TransportFactory = Callable[[RequestHandler], httpx.MockTransport]


@pytest.fixture(autouse=True)
def _isolated_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Run every test from a clean working directory.

    ``AppConfig`` / ``LoginSettings`` (pydantic-settings) read ``.env`` relative
    to the cwd. Without this, a developer's real project ``.env`` -- e.g. one
    written by ``python -m novus_receipts.login`` -- would leak tokens into
    tests and break the "no token configured" cases. Tests that need a dotenv
    create their own under ``tmp_path`` and pass it explicitly.
    """

    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _clear_novus_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every NOVUS_* var so the host environment never leaks into a test.

    Tests that need a value set it themselves (or build the config explicitly).
    """

    for name in NOVUS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class RequestRecorder:
    """A ``MockTransport`` handler that records each request and replies canned JSON."""

    def __init__(self, json_data: Any = None, status_code: int = 200) -> None:
        self.json_data = json_data
        self.status_code = status_code
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status_code, json=self.json_data)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def query_of(request: httpx.Request) -> dict[str, list[str]]:
    """Return a request's parsed query string as ``{key: [values]}``."""

    return parse_qs(urlsplit(str(request.url)).query, keep_blank_values=True)


def assert_constant_headers(request: httpx.Request) -> None:
    """Every request must carry the three constant Novus headers."""

    assert request.headers["Platform"] == "android"
    assert request.headers["PlatformVersion"] == "14 (34)"
    assert request.headers["private_key"] == PRIVATE_KEY


def assert_no_user_token(request: httpx.Request) -> None:
    """The request must not carry the per-call ``user_token`` auth header."""

    assert "user_token" not in request.headers


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
