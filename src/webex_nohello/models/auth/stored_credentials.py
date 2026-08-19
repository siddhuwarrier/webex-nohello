"""Everything persisted to the OS keychain, as one versioned record.

Extra fields are forbidden so that a format change fails loudly rather than silently
dropping a token (Article V.2).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from webex_nohello.models.auth.oauth_app import OAuthApp
from webex_nohello.models.auth.token_set import TokenSet

CREDENTIAL_FORMAT_VERSION: Literal[1] = 1


class StoredCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = CREDENTIAL_FORMAT_VERSION
    app: OAuthApp
    tokens: TokenSet
    person_email: str
    person_display_name: str
