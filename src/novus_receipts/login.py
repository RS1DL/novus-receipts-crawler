"""Interactive OTP login CLI: ``python -m novus_receipts.login`` (NOVUS_API.md §2).

Prompts for a phone number, requests an OTP (SMS), prompts for the code,
confirms it, and saves the resulting ``token`` / ``refresh_token`` into a dotenv
file (default ``.env``) as ``NOVUS_USER_TOKEN`` / ``NOVUS_REFRESH_TOKEN`` so the
crawler can pick them up.

``input`` / ``print`` are injected (``input_fn`` / ``print_fn``) so the flow is
fully testable without real stdin/stdout; the testable path never calls the
builtins directly. Tokens are masked when echoed -- only the last 4 chars are
ever printed.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from novus_receipts.auth.login import LoginClient, LoginSettings, write_tokens_to_env


def _mask(token: str) -> str:
    """Render a token as ``****`` + its last 4 chars, hiding the secret.

    A short token (8 chars or fewer) is fully masked so its body never leaks
    through the last-4 tail.
    """

    return "****" + token[-4:] if len(token) > 8 else "****"


def main(
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[..., None] = print,
    env_path: str = ".env",
    http_client: httpx.Client | None = None,
    settings: LoginSettings | None = None,
) -> int:
    """Run the interactive login flow; persist masked tokens; return ``0``.

    Builds ``settings`` (``LoginSettings()`` if omitted) and owns an
    :class:`httpx.Client` (built from the settings) when ``http_client`` is not
    injected, closing it in ``finally``. Prompts via ``input_fn``, prints via
    ``print_fn``.
    """

    cfg = settings if settings is not None else LoginSettings()
    client = http_client or httpx.Client(base_url=cfg.base_url, timeout=cfg.timeout_s)
    try:
        login = LoginClient(client, cfg)

        phone = input_fn("Phone number: ").strip()
        login.request_otp(phone)

        otp = input_fn("SMS code: ").strip()
        session = login.confirm_otp(otp, phone)

        write_tokens_to_env(
            env_path,
            user_token=session.token,
            refresh_token=session.refresh_token,
        )

        print_fn(f"Logged in as {session.user_first_name} (bonuses: {session.bonuses}).")
        print_fn(
            f"Saved user_token={_mask(session.token)} "
            f"refresh_token={_mask(session.refresh_token)} to {env_path}"
        )
        return 0
    finally:
        if http_client is None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
