"""What arrived on the loopback redirect: either an authorization code, or a reason it failed.

Exactly one of the two fields is set.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CallbackOutcome:
    code: str | None = None
    failure: str | None = None
    remediation: str | None = None
