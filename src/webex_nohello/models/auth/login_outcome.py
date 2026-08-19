"""The result of a completed sign-in."""

from __future__ import annotations

from dataclasses import dataclass

from webex_nohello.models.auth.stored_credentials import StoredCredentials
from webex_nohello.models.webex.person import Person


@dataclass(frozen=True)
class LoginOutcome:
    person: Person
    credentials: StoredCredentials
