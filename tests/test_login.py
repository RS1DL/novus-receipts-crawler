"""Tests for the OTP login feature (auth/login.py + login.py CLI).

All tests are deterministic: HTTP is driven through ``httpx.MockTransport`` (via
``make_mock_client``), input/print are scripted fakes, and dotenv files live in
``tmp_path``. No real network or stdin/stdout is touched.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from novus_receipts import login as login_cli
from novus_receipts.auth.login import (
    LoginClient,
    LoginSettings,
    normalize_phone,
    write_tokens_to_env,
)
from novus_receipts.dto.auth import (
    AuthTokenResponse,
    CheckNumberResponse,
    ConfirmWithOtpResponse,
    OtpChallengeResponse,
)
from novus_receipts.errors import NovusApiError, NovusAuthError
from tests.conftest import (
    BASE_URL,
    PRIVATE_KEY,
    RequestRecorder,
    assert_constant_headers,
    assert_no_user_token,
)


def build_login_client(
    make_mock_client: Callable[..., httpx.Client],
    *,
    json_data: object = None,
    status_code: int = 200,
    settings: LoginSettings | None = None,
) -> tuple[LoginClient, RequestRecorder]:
    """Wire a recorder-backed ``httpx.Client`` into a ``LoginClient``."""

    cfg = settings if settings is not None else LoginSettings()
    recorder = RequestRecorder(json_data, status_code)
    http = make_mock_client(recorder, base_url=cfg.base_url)
    return LoginClient(http, cfg), recorder


def _confirm_payload() -> dict[str, object]:
    return {
        "token": "access-XYZ7890",
        "refresh_token": "refresh-ABC1234",
        "first_authorization": True,
        "user_first_name": "Ann",
        "bonuses": "10.00",
        "bonus_type": "standard",
        "blocked_card": False,
    }


# --- LoginSettings ----------------------------------------------------------


def test_login_settings_requires_nothing_and_has_defaults() -> None:
    cfg = LoginSettings()

    assert cfg.base_url == BASE_URL
    assert cfg.private_key == PRIVATE_KEY
    assert cfg.platform_version == "14 (34)"
    assert cfg.timeout_s == 30


def test_login_settings_platform_is_constant_and_not_env_overridable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOVUS_PLATFORM", "ios")

    cfg = LoginSettings()

    # platform is a ClassVar constant: env cannot override it.
    assert cfg.platform == "android"


# --- check_number -----------------------------------------------------------


def test_check_number_request_and_dto(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_login_client(
        make_mock_client, json_data={"user_exists": True}
    )

    result = client.check_number("380501112233")

    req = recorder.last
    assert req.method == "POST"
    assert urlsplit(str(req.url)).path == "/auth/check_number"
    assert json.loads(req.content) == {"phone": "380501112233"}
    assert_constant_headers(req)
    assert_no_user_token(req)
    assert isinstance(result, CheckNumberResponse)
    assert result.user_exists is True


# --- create_auth_token (NOVUS_API.md §2 step 2, optional) -------------------


def test_create_auth_token_request_and_dto(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_login_client(
        make_mock_client, json_data={"auth_token": "intermediate-AT"}
    )

    result = client.create_auth_token("g-123", "380501112233")

    req = recorder.last
    assert req.method == "POST"
    assert urlsplit(str(req.url)).path == "/auth/auth_token"
    assert json.loads(req.content) == {"google_id": "g-123", "phone": "380501112233"}
    assert_constant_headers(req)
    assert_no_user_token(req)
    assert isinstance(result, AuthTokenResponse)
    assert result.auth_token == "intermediate-AT"


# --- request_otp ------------------------------------------------------------


def _otp_payload() -> dict[str, object]:
    return {"message": "sent", "ttl": 60, "attempts_left": 3}


def test_request_otp_minimal_body_omits_optionals(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_login_client(make_mock_client, json_data=_otp_payload())

    result = client.request_otp("380501112233")

    req = recorder.last
    assert req.method == "POST"
    assert urlsplit(str(req.url)).path == "/auth/check_user_by_phone"
    body = json.loads(req.content)
    assert body == {"phone": "380501112233"}
    assert "auth_token" not in body
    assert "google_id" not in body
    assert_constant_headers(req)
    assert_no_user_token(req)
    assert isinstance(result, OtpChallengeResponse)
    assert result.ttl == 60
    assert result.attempts_left == 3


def test_request_otp_includes_optionals_when_given(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_login_client(make_mock_client, json_data=_otp_payload())

    client.request_otp("380501112233", auth_token="AT", google_id="GID")

    body = json.loads(recorder.last.content)
    assert body == {
        "phone": "380501112233",
        "auth_token": "AT",
        "google_id": "GID",
    }


# --- resend_otp -------------------------------------------------------------


def test_resend_otp_minimal_body_omits_optionals(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_login_client(make_mock_client, json_data=_otp_payload())

    result = client.resend_otp("380501112233")

    req = recorder.last
    assert req.method == "POST"
    assert urlsplit(str(req.url)).path == "/auth/resend_otp"
    body = json.loads(req.content)
    assert body == {"phone": "380501112233"}
    assert "auth_token" not in body
    assert "google_id" not in body
    assert_constant_headers(req)
    assert_no_user_token(req)
    assert isinstance(result, OtpChallengeResponse)
    assert result.message == "sent"


def test_resend_otp_includes_optionals_when_given(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_login_client(make_mock_client, json_data=_otp_payload())

    client.resend_otp("380501112233", auth_token="AT", google_id="GID")

    body = json.loads(recorder.last.content)
    assert body == {
        "phone": "380501112233",
        "auth_token": "AT",
        "google_id": "GID",
    }


# --- confirm_otp ------------------------------------------------------------


def test_confirm_otp_minimal_body_and_dto(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_login_client(make_mock_client, json_data=_confirm_payload())

    result = client.confirm_otp("1234", "380501112233")

    req = recorder.last
    assert req.method == "POST"
    assert urlsplit(str(req.url)).path == "/auth/confirm_with_otp"
    body = json.loads(req.content)
    assert body == {"otp": "1234", "phone": "380501112233"}
    assert "referral_user_id" not in body
    assert "auth_token" not in body
    assert "google_id" not in body
    assert_constant_headers(req)
    assert_no_user_token(req)
    assert isinstance(result, ConfirmWithOtpResponse)
    assert result.token == "access-XYZ7890"
    assert result.refresh_token == "refresh-ABC1234"


def test_confirm_otp_includes_optionals_when_given(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, recorder = build_login_client(make_mock_client, json_data=_confirm_payload())

    client.confirm_otp(
        "1234",
        "380501112233",
        referral_user_id="REF",
        auth_token="AT",
        google_id="GID",
    )

    body = json.loads(recorder.last.content)
    assert body == {
        "otp": "1234",
        "phone": "380501112233",
        "referral_user_id": "REF",
        "auth_token": "AT",
        "google_id": "GID",
    }


# --- error classification ---------------------------------------------------


def test_confirm_otp_bad_code_surfaces_auth_error(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, _ = build_login_client(
        make_mock_client, json_data={"message": "invalid otp"}, status_code=401
    )

    with pytest.raises(NovusAuthError):
        client.confirm_otp("0000", "380501112233")


def test_confirm_otp_4xx_surfaces_api_error(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    client, _ = build_login_client(
        make_mock_client, json_data={"message": "bad request"}, status_code=400
    )

    with pytest.raises(NovusApiError):
        client.confirm_otp("0000", "380501112233")


def test_error_checked_before_parsing(
    make_mock_client: Callable[..., httpx.Client],
) -> None:
    # An empty/non-DTO body on a 401 must raise the auth error, not a
    # validation error -- proving raise_for_response runs before model_validate.
    client, _ = build_login_client(make_mock_client, json_data=None, status_code=401)

    with pytest.raises(NovusAuthError):
        client.request_otp("380501112233")


# --- write_tokens_to_env ----------------------------------------------------


def test_write_tokens_creates_new_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"

    write_tokens_to_env(env, user_token="tok-1", refresh_token="ref-1")

    text = env.read_text(encoding="utf-8")
    assert "NOVUS_USER_TOKEN=tok-1" in text
    assert "NOVUS_REFRESH_TOKEN=ref-1" in text


def test_write_tokens_preserves_other_lines_and_comments(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# my config\nNOVUS_BASE_URL=https://api.novus.online\nOTHER=keep\n",
        encoding="utf-8",
    )

    write_tokens_to_env(env, user_token="tok-1", refresh_token="ref-1")

    lines = env.read_text(encoding="utf-8").splitlines()
    assert "# my config" in lines
    assert "NOVUS_BASE_URL=https://api.novus.online" in lines
    assert "OTHER=keep" in lines
    assert "NOVUS_USER_TOKEN=tok-1" in lines
    assert "NOVUS_REFRESH_TOKEN=ref-1" in lines


def test_write_tokens_replaces_existing_keys_without_duplicates(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "NOVUS_USER_TOKEN=old-tok\nKEEP=1\nNOVUS_REFRESH_TOKEN=old-ref\n",
        encoding="utf-8",
    )

    write_tokens_to_env(env, user_token="new-tok", refresh_token="new-ref")

    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines.count("NOVUS_USER_TOKEN=new-tok") == 1
    assert lines.count("NOVUS_REFRESH_TOKEN=new-ref") == 1
    assert "old-tok" not in env.read_text(encoding="utf-8")
    assert "old-ref" not in env.read_text(encoding="utf-8")
    assert "KEEP=1" in lines
    # Replaced in place: the user-token line stays first.
    assert lines[0] == "NOVUS_USER_TOKEN=new-tok"


def test_write_tokens_collapses_preexisting_duplicate_keys(tmp_path: Path) -> None:
    # A file hand-edited to contain duplicate keys must end up with exactly one
    # of each managed key holding the new value (python-dotenv resolves dupes to
    # the LAST line, so a surviving stale line would silently win on reload).
    env = tmp_path / ".env"
    env.write_text(
        "NOVUS_USER_TOKEN=old-a\nKEEP=1\nNOVUS_USER_TOKEN=old-b\n",
        encoding="utf-8",
    )

    write_tokens_to_env(env, user_token="new-tok", refresh_token="new-ref")

    text = env.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines.count("NOVUS_USER_TOKEN=new-tok") == 1
    assert lines.count("NOVUS_REFRESH_TOKEN=new-ref") == 1
    assert "old-a" not in text
    assert "old-b" not in text
    assert "KEEP=1" in lines


def test_write_tokens_is_idempotent(tmp_path: Path) -> None:
    env = tmp_path / ".env"

    write_tokens_to_env(env, user_token="tok-1", refresh_token="ref-1")
    first = env.read_text(encoding="utf-8")
    write_tokens_to_env(env, user_token="tok-1", refresh_token="ref-1")
    second = env.read_text(encoding="utf-8")

    assert first == second
    lines = second.splitlines()
    assert lines.count("NOVUS_USER_TOKEN=tok-1") == 1
    assert lines.count("NOVUS_REFRESH_TOKEN=ref-1") == 1


# --- CLI main() -------------------------------------------------------------


class ScriptedInput:
    """Returns queued answers in order, ignoring the prompt text."""

    def __init__(self, *answers: str) -> None:
        self._answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._answers.pop(0)


class CapturePrint:
    """Captures everything ``print`` would have emitted, as one joined string."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, *args: object, **kwargs: object) -> None:
        self.lines.append(" ".join(str(a) for a in args))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def test_main_happy_path_writes_tokens_and_masks_output(
    make_mock_client: Callable[..., httpx.Client],
    tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    recorder = RequestRecorder(_confirm_payload())
    # request_otp returns the otp payload; confirm_otp returns the session.
    # Use a handler that routes by path so both calls get a sane body.
    otp_body = _otp_payload()
    confirm_body = _confirm_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        path = urlsplit(str(request.url)).path
        if path == "/auth/auth_token":
            return httpx.Response(200, json={"auth_token": "AT-token"})
        if path == "/auth/check_user_by_phone":
            return httpx.Response(200, json=otp_body)
        if path == "/auth/confirm_with_otp":
            return httpx.Response(200, json=confirm_body)
        return httpx.Response(404, json={})

    http = make_mock_client(handler, base_url=BASE_URL)
    # OTP "9999" deliberately differs from every token tail so a mask assertion
    # cannot be satisfied by the echoed code.
    inputs = ScriptedInput("380501112233", "9999")
    printer = CapturePrint()

    rc = login_cli.main(
        input_fn=inputs,
        print_fn=printer,
        env_path=str(env),
        http_client=http,
        google_id="g-test",
    )

    assert rc == 0

    # Tokens persisted to the env file.
    text = env.read_text(encoding="utf-8")
    assert "NOVUS_USER_TOKEN=access-XYZ7890" in text
    assert "NOVUS_REFRESH_TOKEN=refresh-ABC1234" in text

    # Output is informative but masks the full secrets.
    out = printer.text
    assert "Ann" in out
    # The misleading "starting bonuses" field must NOT be presented as a balance.
    assert "bonuses" not in out.lower()
    assert str(env) in out
    assert "access-XYZ7890" not in out
    assert "refresh-ABC1234" not in out
    # Exactly the masked forms appear (the "****" prefix proves it is the mask,
    # not a bare echo of the token tail or the OTP code).
    assert "****7890" in out
    assert "****1234" in out

    # All three OTP calls were made, in order, without user_token.
    paths = [urlsplit(str(r.url)).path for r in recorder.requests]
    assert paths == [
        "/auth/auth_token",
        "/auth/check_user_by_phone",
        "/auth/confirm_with_otp",
    ]
    for r in recorder.requests:
        assert_no_user_token(r)
        assert_constant_headers(r)


def test_main_passes_phone_and_otp_through(
    make_mock_client: Callable[..., httpx.Client],
    tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    bodies: list[dict[str, object]] = []
    confirm_body = _confirm_payload()
    otp_body = _otp_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        path = urlsplit(str(request.url)).path
        bodies.append(json.loads(request.content))
        if path == "/auth/auth_token":
            return httpx.Response(200, json={"auth_token": "AT-xyz"})
        if path == "/auth/check_user_by_phone":
            return httpx.Response(200, json=otp_body)
        return httpx.Response(200, json=confirm_body)

    http = make_mock_client(handler, base_url=BASE_URL)

    login_cli.main(
        input_fn=ScriptedInput("380999999999", "5678"),
        print_fn=CapturePrint(),
        env_path=str(env),
        http_client=http,
        google_id="g-xyz",
    )

    # 1) auth_token {phone, google_id}; 2) check_user_by_phone adds the obtained
    # auth_token; 3) confirm adds otp + the same auth_token + google_id.
    assert bodies[0] == {"phone": "380999999999", "google_id": "g-xyz"}
    assert bodies[1] == {
        "phone": "380999999999",
        "google_id": "g-xyz",
        "auth_token": "AT-xyz",
    }
    assert bodies[2] == {
        "otp": "5678",
        "phone": "380999999999",
        "auth_token": "AT-xyz",
        "google_id": "g-xyz",
    }


# --- normalize_phone --------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "+380631234567",
        "380631234567",
        "0631234567",
        "631234567",
        "00380631234567",
        "+380 63 123 45 67",
        " +380-63-123-45-67 ",
    ],
)
def test_normalize_phone_maps_common_shapes_to_country_code_form(raw: str) -> None:
    # The API expects "380XXXXXXXXX" (no '+', no spaces); see the live 400
    # "Invalid parameters passed: phone" for the '+'-prefixed form.
    assert normalize_phone(raw) == "380631234567"


# --- _mask ------------------------------------------------------------------


def test_mask_reveals_only_last_four_of_a_long_token() -> None:
    assert login_cli._mask("access-XYZ7890") == "****7890"


def test_mask_fully_hides_short_tokens() -> None:
    # A short token must never leak its body through the last-4 tail.
    for short in ("", "ab", "abcd", "12345678"):
        masked = login_cli._mask(short)
        assert masked == "****"
        assert short == "" or short not in masked
