"""The OS keychain could not be read, written or cleared."""

from __future__ import annotations

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError


class CredentialStorageError(WebexNoHelloError):
    pass
