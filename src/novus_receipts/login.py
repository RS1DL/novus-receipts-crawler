"""Interactive OTP login CLI: ``python -m novus_receipts.login`` (NOVUS_API.md §2).

Drives the full three-step Novus OTP flow (verified against the live API):

1. ``POST /auth/auth_token  {phone, google_id}``        -> an ``auth_token``;
2. ``POST /auth/check_user_by_phone {phone, google_id, auth_token}`` -> sends SMS;
3. ``POST /auth/confirm_with_otp {otp, phone, google_id, auth_token}`` -> session.

The same ``auth_token`` and ``google_id`` are threaded through all three calls
(the server rejects the SMS step without them). The resulting
``token`` / ``refresh_token`` are saved into a dotenv file (default ``.env``) as
``NOVUS_USER_TOKEN`` / ``NOVUS_REFRESH_TOKEN`` so the crawler can pick them up.

``input`` / ``print`` are injected (``input_fn`` / ``print_fn``) so the flow is
fully testable without real stdin/stdout; the testable path never calls the
builtins directly. Tokens are masked when echoed -- only the last 4 chars are
ever printed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx

from novus_receipts.auth.login import (
    LoginClient,
    LoginSettings,
    normalize_phone,
    write_tokens_to_env,
)


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
    google_id: str | None = None,
) -> int:
    """Run the interactive login flow; persist masked tokens; return ``0``.

    Builds ``settings`` (``LoginSettings()`` if omitted) and owns an
    :class:`httpx.Client` (built from the settings) when ``http_client`` is not
    injected, closing it in ``finally``. Prompts via ``input_fn``, prints via
    ``print_fn``. The ``google_id`` device identifier comes from the argument,
    then ``settings.google_id`` (``NOVUS_GOOGLE_ID``), else a fresh random id;
    the same value (and the obtained ``auth_token``) are threaded through all
    three OTP calls.
    """

    cfg = settings if settings is not None else LoginSettings()
    client = http_client or httpx.Client(base_url=cfg.base_url, timeout=cfg.timeout_s)
    try:
        login = LoginClient(client, cfg)
        gid = google_id or cfg.google_id or uuid.uuid4().hex

        phone = normalize_phone(input_fn("Phone number (e.g. +380631234567): "))
        auth_token = login.create_auth_token(gid, phone).auth_token
        login.request_otp(phone, auth_token=auth_token, google_id=gid)

        otp = input_fn("SMS code: ").strip()
        session = login.confirm_otp(otp, phone, auth_token=auth_token, google_id=gid)

        write_tokens_to_env(
            env_path,
            user_token=session.token,
            refresh_token=session.refresh_token,
        )

        # NB: session.bonuses is the confirm response's "starting bonuses" field,
        # NOT the wallet balance (that lives at GET /user/bonuses/current), so it
        # is intentionally not shown here to avoid confusion.
        print_fn(f"Logged in as {session.user_first_name}.")
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
