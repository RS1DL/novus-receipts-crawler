"""T0.1 / T0.2 smoke tests: package imports and test infrastructure works."""

from __future__ import annotations

import httpx

import novus_receipts


def test_package_has_version() -> None:
    assert novus_receipts.__version__
    assert isinstance(novus_receipts.__version__, str)


def test_make_mock_client_returns_configured_response(make_mock_client) -> None:
    client = make_mock_client(json_data={"hello": "world"})
    response = client.get("https://example.test/anything")
    assert response.status_code == 200
    assert response.json() == {"hello": "world"}


def test_make_mock_client_accepts_custom_handler(make_mock_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/echo"
        return httpx.Response(201, json={"path": request.url.path})

    client = make_mock_client(handler)
    response = client.get("https://example.test/echo")
    assert response.status_code == 201
    assert response.json() == {"path": "/echo"}


def test_load_fixture_reads_json(load_fixture, tmp_path, monkeypatch) -> None:
    import tests.conftest as conftest

    (tmp_path / "sample.json").write_text('{"a": 1}', encoding="utf-8")
    monkeypatch.setattr(conftest, "FIXTURES_DIR", tmp_path)
    assert load_fixture("sample.json") == {"a": 1}


def test_fake_sleeper_records_calls_without_sleeping(fake_sleeper) -> None:
    fake_sleeper(0.2)
    fake_sleeper(1.5)
    assert fake_sleeper.calls == [0.2, 1.5]
