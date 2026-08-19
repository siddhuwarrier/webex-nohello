"""The Webex authorization code flow.

The SDK performs the token exchange and refresh, but provides no authorize-URL builder
and no redirect receiver, so those stay here along with the `state` check.

PKCE is not used: the SDK's AccessTokensAPI.get() takes no code_verifier. See the
recorded deviation in Appendix A of the constitution.
"""

from __future__ import annotations

import base64
import http.server
import secrets
import urllib.parse
from urllib.parse import quote

from pydantic import ValidationError
from webexpythonsdk.api.access_tokens import AccessTokensAPI
from webexpythonsdk.config import DEFAULT_BASE_URL
from webexpythonsdk.exceptions import ApiError
from webexpythonsdk.models.immutable import immutable_data_factory

from webex_nohello.clock import Clock
from webex_nohello.models.auth.authorization_request import AuthorizationRequest
from webex_nohello.models.auth.callback_outcome import CallbackOutcome
from webex_nohello.models.auth.oauth_app import OAuthApp
from webex_nohello.models.auth.token_response import TokenResponse
from webex_nohello.models.auth.token_set import TokenSet
from webex_nohello.models.errors.oauth_error import OAuthError
from webex_nohello.scopes import REQUIRED_SCOPES, scope_parameter

AUTHORIZE_ENDPOINT = "https://webexapis.com/v1/authorize"
CALLBACK_PATH = "/callback"
DEFAULT_PORT = 8090
STATE_BYTES = 32
REQUEST_TIMEOUT_SECONDS = 15


def redirect_uri(port: int) -> str:
    return f"http://localhost:{port}{CALLBACK_PATH}"


def _encode_query(params: dict[str, str]) -> str:
    """Percent-encode a query string, encoding spaces as %20 rather than as `+`.

    `urlencode` form-encodes, which turns the spaces between scopes into `+`. RFC 6749
    defines `scope` as space-delimited, and a server that percent-decodes strictly reads
    a literal `+` as part of the scope name — yielding one nonexistent scope and an
    `invalid_scope` rejection. Every other value encodes identically to `urlencode`.
    """
    return "&".join(f"{key}={quote(value, safe='')}" for key, value in params.items())


class OAuthService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        # Constructed without a token on purpose: this is the one API that runs before
        # any token exists. The SDK builds it before its own access-token check for the
        # same reason.
        self._tokens = AccessTokensAPI(
            # Must be a real URL: AccessTokensAPI calls validate_base_url, which rejects
            # None. WebexAPI passes this same constant.
            base_url=DEFAULT_BASE_URL,
            object_factory=immutable_data_factory,
            single_request_timeout=REQUEST_TIMEOUT_SECONDS,
        )

    def authorization_request(self, app: OAuthApp, callback_url: str) -> AuthorizationRequest:
        state = base64.urlsafe_b64encode(secrets.token_bytes(STATE_BYTES)).decode().rstrip("=")
        params = {
            "client_id": app.client_id,
            "response_type": "code",
            "redirect_uri": callback_url,
            "scope": scope_parameter(),
            "state": state,
        }
        url = f"{AUTHORIZE_ENDPOINT}?{_encode_query(params)}"
        return AuthorizationRequest(url=url, state=state)

    def exchange_code(self, app: OAuthApp, *, code: str, callback_url: str) -> TokenSet:
        try:
            issued = self._tokens.get(
                client_id=app.client_id,
                client_secret=app.client_secret.get_secret_value(),
                code=code,
                redirect_uri=callback_url,
            )
        except ApiError as exc:
            raise OAuthError(
                f"Webex refused to exchange the authorization code: {exc}",
                remediation=(
                    "Check the client ID and secret match the integration, and that the "
                    "integration's redirect URI is exactly the one shown during login."
                ),
            ) from exc
        return self._to_token_set(issued.json_data)

    def refresh(self, app: OAuthApp, refresh_token: str) -> TokenSet:
        try:
            issued = self._tokens.refresh(
                client_id=app.client_id,
                client_secret=app.client_secret.get_secret_value(),
                refresh_token=refresh_token,
            )
        except ApiError as exc:
            raise OAuthError(
                f"Webex refused to refresh the access token: {exc}",
                remediation="Run 'webex-nohello auth login' to sign in again.",
            ) from exc
        return self._to_token_set(issued.json_data)

    def _to_token_set(self, payload: object) -> TokenSet:
        try:
            parsed = TokenResponse.model_validate(payload)
        except ValidationError as exc:
            raise OAuthError(
                f"Webex returned a token response this program does not understand: {exc}",
                remediation="Report this with the Webex response shape.",
            ) from exc
        return parsed.to_token_set(self._clock(), requested_scopes=scope_parameter())


_SUCCESS_PAGE = b"""<!doctype html><meta charset="utf-8"><title>webex-nohello</title>
<body style="font:16px system-ui;margin:4rem auto;max-width:32rem">
<h1>Signed in</h1><p>You can close this tab and return to the terminal.</p>
"""

_FAILURE_PAGE = b"""<!doctype html><meta charset="utf-8"><title>webex-nohello</title>
<body style="font:16px system-ui;margin:4rem auto;max-width:32rem">
<h1>Sign-in failed</h1><p>Return to the terminal for the details.</p>
"""


def interpret_callback(params: dict[str, list[str]], *, expected_state: str) -> CallbackOutcome:
    """Turn callback query parameters into either an authorization code or a reason."""
    error = _first(params, "error")
    if error is not None:
        description = _first(params, "error_description") or "no description given"
        return CallbackOutcome(
            failure=f"Webex returned an authorization error: {error} ({description})",
            remediation=_remediation_for(error),
        )

    if _first(params, "state") != expected_state:
        return CallbackOutcome(
            failure=(
                "The state parameter on the callback did not match the one sent. "
                "The sign-in was abandoned rather than trusted."
            )
        )

    code = _first(params, "code")
    if code is None:
        return CallbackOutcome(failure="The callback carried no authorization code.")
    return CallbackOutcome(code=code)


def _first(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    return values[0] if values else None


def _remediation_for(error: str) -> str:
    """Turn an OAuth error code into the specific thing to go and check.

    `invalid_scope` earns its own text because it is by far the most likely first-run
    failure, and it means one specific thing: the integration was registered without
    every scope this program asks for. Saying "try again" instead wastes the operator's
    afternoon.
    """
    if error == "invalid_scope":
        wanted = "\n    ".join(scope.name for scope in REQUIRED_SCOPES)
        return (
            "The integration was not registered with every scope this program needs.\n"
            "  Open https://developer.webex.com/my-apps, edit the integration, and "
            "confirm all of these are ticked:\n"
            f"    {wanted}\n"
            "  Then run 'webex-nohello auth login' again."
        )
    if error == "access_denied":
        return "You declined the authorisation. Run 'webex-nohello auth login' to retry."
    if error == "redirect_uri_mismatch":
        return (
            "The integration's redirect URI does not match the one this program sent. "
            "They must be identical, including port and path."
        )
    return "Run 'webex-nohello auth login' again."


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Receives exactly one redirect. `state` is checked here, before the token exchange."""

    expected_state: str
    code: str | None = None
    failure: str | None = None
    remediation: str | None = None

    def do_GET(self) -> None:  # the stdlib dispatches on this exact name
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_error(http.HTTPStatus.NOT_FOUND)
            return

        outcome = interpret_callback(
            urllib.parse.parse_qs(parsed.query), expected_state=type(self).expected_state
        )
        if outcome.code is not None:
            type(self).code = outcome.code
            self._respond(http.HTTPStatus.OK, _SUCCESS_PAGE)
        else:
            type(self).failure = outcome.failure
            type(self).remediation = outcome.remediation
            self._respond(http.HTTPStatus.BAD_REQUEST, _FAILURE_PAGE)

    def _respond(self, status: http.HTTPStatus, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silence the stdlib access log; it would interleave with our own output."""


def wait_for_code(*, port: int, expected_state: str, timeout_seconds: float) -> str:
    # A per-call subclass, so the expected state is bound without a module-level global
    # and two concurrent sign-ins could not read each other's.
    handler: type[_CallbackHandler] = type(
        "_BoundCallbackHandler", (_CallbackHandler,), {"expected_state": expected_state}
    )

    with http.server.HTTPServer(("127.0.0.1", port), handler) as server:
        server.timeout = timeout_seconds
        server.handle_request()

    if handler.code is not None:
        return handler.code

    if handler.failure is not None:
        raise OAuthError(handler.failure, remediation=handler.remediation)

    raise OAuthError(
        f"No OAuth callback arrived within {timeout_seconds:.0f} seconds.",
        remediation=(
            f"Check the integration's redirect URI is exactly {redirect_uri(port)} and "
            "that nothing else is bound to that port."
        ),
    )
