"""How far this program has read in one space.

Webex exposes no unread count and no last-seen pointer, so "unread" is defined locally
by this record (Article VI.1). It is the most safety-critical state the program keeps:
lose it and the next run treats the whole history as new.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class HighWaterMark(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str
    message_id: str
    # Kept alongside the id because ids are opaque and cannot be compared for recency;
    # this is what makes a mark readable by a human debugging a missed reply.
    created: datetime | None = None
