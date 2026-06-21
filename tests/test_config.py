"""Tests for AppConfig (T1.1 defaults, T1.2 from_env/required, T1.3 refresh).

Deterministic and isolated from the host environment: every test that relies on
defaults or required-field behaviour first strips all ``NOVUS_*`` env vars so the
real environment (or a developer ``.env``) cannot leak in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from novus_receipts.config import DEFAULT_PRIVATE_KEY, AppConfig

# NOVUS_* env vars are cleared globally by the conftest autouse fixture, so each
# test sees a clean environment without any per-test setup.


# ---------------------------------------------------------------------------
# T1.1 — defaults match PLAN.md §6
# ---------------------------------------------------------------------------


def test_defaults_match_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    config = AppConfig(user_token="tok")

    assert config.base_url == "https://api.novus.online"
    assert config.user_token == "tok"
    assert config.refresh_token is None
    assert config.private_key == DEFAULT_PRIVATE_KEY
    assert config.platform == "android"
    assert config.platform_version == "14 (34)"
    assert config.timeout_s == 30
    assert config.max_retries == 3
    assert config.backoff_base_s == 1.0
    assert config.request_delay_s == 0.2
    assert config.detail_concurrency == 1
    assert config.timezone == "Europe/Kyiv"
    assert config.page_size == 100


def test_timezone_overridable_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVUS_USER_TOKEN", "tok")
    monkeypatch.setenv("NOVUS_TIMEZONE", "UTC")

    assert AppConfig().timezone == "UTC"


def test_page_size_overridable_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVUS_USER_TOKEN", "tok")
    monkeypatch.setenv("NOVUS_PAGE_SIZE", "250")

    assert AppConfig().page_size == 250


def test_default_field_types(monkeypatch: pytest.MonkeyPatch) -> None:
    config = AppConfig(user_token="tok")

    assert isinstance(config.timeout_s, float)
    assert isinstance(config.backoff_base_s, float)
    assert isinstance(config.request_delay_s, float)
    assert isinstance(config.max_retries, int)
    assert isinstance(config.detail_concurrency, int)


# ---------------------------------------------------------------------------
# T1.2 — env override, required user_token, .env reading, from_env
# ---------------------------------------------------------------------------


def test_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVUS_USER_TOKEN", "tok")
    monkeypatch.setenv("NOVUS_BASE_URL", "https://staging.novus.test")
    monkeypatch.setenv("NOVUS_TIMEOUT_S", "5")
    monkeypatch.setenv("NOVUS_MAX_RETRIES", "7")

    config = AppConfig()

    assert config.base_url == "https://staging.novus.test"
    assert config.timeout_s == 5
    assert config.max_retries == 7
    # Untouched fields keep their defaults.
    assert config.private_key == DEFAULT_PRIVATE_KEY
    assert config.platform == "android"


def test_user_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVUS_USER_TOKEN", "from-env-token")

    config = AppConfig()

    assert config.user_token == "from-env-token"


def test_missing_user_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        AppConfig()


def test_missing_user_token_error_mentions_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        AppConfig()
    assert "user_token" in str(exc_info.value)


def test_platform_is_constant_and_ignores_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PLAN.md §6 lists `platform` with no env name (a constant): NOVUS_PLATFORM
    # must not override it.
    monkeypatch.setenv("NOVUS_USER_TOKEN", "tok")
    monkeypatch.setenv("NOVUS_PLATFORM", "ios-injected")

    config = AppConfig()

    assert config.platform == "android"


def test_from_env_builds_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVUS_USER_TOKEN", "env-tok")
    monkeypatch.setenv("NOVUS_REFRESH_TOKEN", "env-refresh")

    config = AppConfig.from_env()

    assert isinstance(config, AppConfig)
    assert config.user_token == "env-tok"
    assert config.refresh_token == "env-refresh"


def test_env_file_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "NOVUS_USER_TOKEN=file-token\n"
        "NOVUS_REFRESH_TOKEN=file-refresh\n"
        "NOVUS_BASE_URL=https://from-file.novus.test\n",
        encoding="utf-8",
    )

    config = AppConfig(_env_file=str(env_file))

    assert config.user_token == "file-token"
    assert config.refresh_token == "file-refresh"
    assert config.base_url == "https://from-file.novus.test"


def test_env_overrides_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("NOVUS_USER_TOKEN=file-token\n", encoding="utf-8")
    monkeypatch.setenv("NOVUS_USER_TOKEN", "real-env-token")

    config = AppConfig(_env_file=str(env_file))

    assert config.user_token == "real-env-token"


def test_extra_env_vars_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVUS_USER_TOKEN", "tok")
    monkeypatch.setenv("NOVUS_UNKNOWN_SETTING", "whatever")

    config = AppConfig()

    assert config.user_token == "tok"
    assert not hasattr(config, "unknown_setting")


# ---------------------------------------------------------------------------
# T1.3 — in-memory refresh token update
# ---------------------------------------------------------------------------


def test_update_refresh_token_changes_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig(user_token="tok", refresh_token="old-refresh")

    config.update_refresh_token("new-refresh")

    assert config.refresh_token == "new-refresh"


def test_update_refresh_token_sets_from_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig(user_token="tok")
    assert config.refresh_token is None

    config.update_refresh_token("fresh")

    assert config.refresh_token == "fresh"


def test_update_refresh_token_keeps_other_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig(user_token="tok", refresh_token="old")

    config.update_refresh_token("new")

    assert config.user_token == "tok"
    assert config.base_url == "https://api.novus.online"
    assert config.private_key == DEFAULT_PRIVATE_KEY
    assert config.platform == "android"
    assert config.platform_version == "14 (34)"
    assert config.timeout_s == 30
    assert config.max_retries == 3
    assert config.backoff_base_s == 1.0
    assert config.request_delay_s == 0.2
    assert config.detail_concurrency == 1
