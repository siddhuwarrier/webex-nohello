"""The authorization code flow failed, at the redirect or at the token exchange."""

from __future__ import annotations

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError


class OAuthError(WebexNoHelloError):
    pass
