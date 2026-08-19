"""The conditions stored credentials can be in.

All but `READY` and `REJECTED` are decidable offline. `REJECTED` means the record looks
sound locally but Webex refused it, which is only discoverable by asking Webex.
"""

from __future__ import annotations

from enum import StrEnum


class CredentialState(StrEnum):
    SIGNED_OUT = "signed_out"
    REFRESH_EXPIRED = "refresh_expired"
    MISSING_SCOPES = "missing_scopes"
    REJECTED = "rejected"
    READY = "ready"
