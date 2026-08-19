"""No usable Webex credentials. Raised by the auth service, caught by commands."""

from __future__ import annotations

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError


class NotAuthenticatedError(WebexNoHelloError):
    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Not authenticated with Webex: {reason}",
            remediation="Run 'webex-nohello auth login' to sign in.",
        )
