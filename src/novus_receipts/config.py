"""Application configuration (PLAN.md §6).

``AppConfig`` collects every runtime setting (base URL, tokens, timeouts, retry
policy, rate-limit guards) from the environment and an optional ``.env`` file
into a single object. All env vars are prefixed ``NOVUS_`` (e.g.
``NOVUS_USER_TOKEN`` -> ``user_token``).

Personal secrets (``user_token`` / ``refresh_token``) come only from the
environment and are never committed. ``private_key`` is a shared, non-personal
application key with a code default that env can still override.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_PRIVATE_KEY = "070696aa5d8844e3c71e90604a7b0a11dc0c99638d3f2e0bf53c2f091ac52d4c"


class AppConfig(BaseSettings):
    """Immutable-ish runtime settings loaded from env / ``.env``.

    The only mutable field is ``refresh_token`` (rotated in memory after a
    reactive refresh via :meth:`update_refresh_token`). ``validate_assignment``
    keeps that assignment type-checked.
    """

    model_config = SettingsConfigDict(
        env_prefix="NOVUS_",
        env_file=".env",
        extra="ignore",
        validate_assignment=True,
    )

    base_url: str = "https://api.novus.online"
    user_token: str
    refresh_token: str | None = None
    private_key: str = DEFAULT_PRIVATE_KEY
    # ``platform`` is a constant (PLAN.md §6 lists it with no env name), so it is
    # a ClassVar rather than a settings field: env (NOVUS_PLATFORM) cannot
    # override it, only the code default applies.
    platform: ClassVar[str] = "android"
    platform_version: str = "14 (34)"
    timeout_s: float = 30
    max_retries: int = 3
    backoff_base_s: float = 1.0
    request_delay_s: float = 0.2
    detail_concurrency: int = 1

    @classmethod
    def from_env(cls) -> AppConfig:
        """Build a config from the environment and ``.env`` file."""
        return cls()  # type: ignore[call-arg]  # fields are populated from env

    def update_refresh_token(self, token: str) -> None:
        """Rotate the in-memory refresh token (e.g. after a reactive refresh)."""
        self.refresh_token = token
