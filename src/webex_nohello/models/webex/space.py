"""A Webex space. This program only ever looks at direct ones."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

DIRECT_SPACE_TYPE = "direct"


class Space(BaseModel):
    # Frozen because a space read from Webex is a value, never edited. That also makes it
    # hashable, so it can key a mapping of space to messages.
    model_config = ConfigDict(extra="ignore", populate_by_name=True, frozen=True)

    id: str
    title: str = ""
    type: str = ""
    last_activity: datetime | None = Field(default=None, alias="lastActivity")

    @property
    def is_direct(self) -> bool:
        return self.type == DIRECT_SPACE_TYPE
