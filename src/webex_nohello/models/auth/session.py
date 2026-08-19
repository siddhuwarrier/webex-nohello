"""A usable, non-expired grant.

Obtainable only from `AuthService.require()`, which is what makes "every Webex call goes
through one authentication check" enforceable by reading the code.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import SecretStr


@dataclass(frozen=True)
class Session:
    access_token: SecretStr
    person_email: str
    person_display_name: str
