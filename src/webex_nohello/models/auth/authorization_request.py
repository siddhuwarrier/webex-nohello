"""A pending authorization request: where to send the operator, and the state to expect back."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str
