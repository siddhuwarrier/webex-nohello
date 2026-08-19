"""A Webex message.

`text` may be absent: a message can be an attachment or a card with no body at all.
Such a message is not a greeting, so it must not be treated as one.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

EXCERPT_LENGTH = 80


class Message(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    room_id: str = Field(default="", alias="roomId")
    person_id: str = Field(default="", alias="personId")
    person_email: str = Field(default="", alias="personEmail")
    text: str = ""
    created: datetime | None = None
    parent_id: str | None = Field(default=None, alias="parentId")

    @property
    def is_thread_reply(self) -> bool:
        return self.parent_id is not None

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())

    def excerpt(self, length: int = EXCERPT_LENGTH) -> str:
        """A short, single-line rendering for logs and dry-run output.

        Article IX.9 caps how much of a message body may be retained anywhere, so this is
        the only form permitted outside debug logging.
        """
        collapsed = " ".join(self.text.split())
        if len(collapsed) <= length:
            return collapsed
        return collapsed[: length - 1] + "…"
