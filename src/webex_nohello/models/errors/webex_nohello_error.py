"""Base for every error this program raises deliberately."""

from __future__ import annotations


class WebexNoHelloError(Exception):
    def __init__(self, message: str, *, remediation: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.remediation = remediation
