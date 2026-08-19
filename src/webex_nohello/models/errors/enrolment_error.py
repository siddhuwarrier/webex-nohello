"""Sign-in completed, but produced a grant this program cannot use."""

from __future__ import annotations

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError


class EnrolmentError(WebexNoHelloError):
    pass
