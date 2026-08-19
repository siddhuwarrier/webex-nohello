"""A held grant: the two tokens, their expiries, and what they are allowed to do."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict

from webex_nohello.models.serialisable_secret import StoredSecret

# Refresh this far ahead of expiry rather than waiting for a 401, so a scheduled run
# never fails its first call (Article VIII.5).
REFRESH_LEEWAY = timedelta(hours=12)


class TokenSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: StoredSecret
    access_token_expires_at: datetime
    refresh_token: StoredSecret
    refresh_token_expires_at: datetime
    granted_scopes: str

    def is_access_token_usable(self, now: datetime) -> bool:
        return now + REFRESH_LEEWAY < self.access_token_expires_at

    def is_refresh_token_usable(self, now: datetime) -> bool:
        return now < self.refresh_token_expires_at
