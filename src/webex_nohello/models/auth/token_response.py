"""What Webex returns from the token endpoint, before it becomes a `TokenSet`."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from webex_nohello.models.auth.token_set import TokenSet


class TokenResponse(BaseModel):
    # Unknown fields are tolerated because upstream adds them (Article V.2).
    model_config = ConfigDict(extra="ignore")

    access_token: SecretStr
    refresh_token: SecretStr
    expires_in: int = Field(gt=0)
    refresh_token_expires_in: int = Field(gt=0)
    scope: str = ""

    def to_token_set(self, now: datetime, *, requested_scopes: str) -> TokenSet:
        return TokenSet(
            access_token=self.access_token,
            access_token_expires_at=now + timedelta(seconds=self.expires_in),
            refresh_token=self.refresh_token,
            refresh_token_expires_at=now + timedelta(seconds=self.refresh_token_expires_in),
            # Webex does not always echo `scope`; fall back to what we asked for so the
            # sufficiency check has something to work with.
            granted_scopes=self.scope or requested_scopes,
        )
