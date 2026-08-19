"""The Webex Integration the operator registered for their own use."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from webex_nohello.models.serialisable_secret import StoredSecret


class OAuthApp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str
    client_secret: StoredSecret
