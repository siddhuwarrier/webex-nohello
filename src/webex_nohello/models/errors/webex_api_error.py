"""A Webex API call was rejected."""

from __future__ import annotations

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError


class WebexApiError(WebexNoHelloError):
    pass
