"""OTP login helpers (NOVUS_API.md §2).

Unlike :class:`novus_receipts.api.client.NovusApiClient`, the login flow needs
no existing ``user_token`` -- none of the ``/auth/*`` login endpoints carry the
``@Authentication`` annotation, so they work with the constant ``private_key``
alone. :class:`LoginClient` (plus :class:`LoginSettings` and
:func:`write_tokens_to_env`) live here precisely so this feature never depends on
``AppConfig`` (whose ``user_token`` is required).
"""

from __future__ import annotations

from novus_receipts.auth.login import (
    LoginClient,
    LoginSettings,
    write_tokens_to_env,
)

__all__ = ["LoginClient", "LoginSettings", "write_tokens_to_env"]
