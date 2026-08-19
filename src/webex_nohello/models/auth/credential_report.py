"""A full assessment of the stored credentials: their state, plus why.

Carries enough detail for `auth status` and `doctor` to explain themselves without
going back to the keychain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from webex_nohello.models.auth.credential_state import CredentialState


@dataclass(frozen=True)
class CredentialReport:
    state: CredentialState
    person_email: str | None = None
    person_display_name: str | None = None
    access_token_expires_at: datetime | None = None
    refresh_token_expires_at: datetime | None = None
    absent_scopes: tuple[str, ...] = field(default_factory=tuple)
    # Why Webex refused the credentials. Set only when the state is REJECTED.
    rejection: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.state is CredentialState.READY
