"""One line of the audit log.

Article X.7 requires timestamp, space, recipient, the message replied to, and the verdict
that justified it. The verdict is the important part: without the model's reasoning, a
misfire discovered a week later cannot be explained, and an unexplainable misfire cannot be
prevented from recurring.

Only a truncated excerpt of the message is kept, per Article IX.9. The audit log is not a
copy of the operator's inbox.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ReplyEvent(StrEnum):
    # Written before the send. Its presence alone blocks any future reply to that person,
    # so a crash between writing and sending costs a reply rather than duplicating one.
    ATTEMPTED = "attempted"
    # Written after a confirmed send. Diagnostics only; cooldowns never consult it.
    SENT = "sent"
    # Written after a send that definitely failed. Also diagnostics only: the attempt
    # stands, and the program will not try again.
    FAILED = "failed"


class ReplyRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: ReplyEvent
    at: datetime
    space_id: str
    recipient_email: str
    replied_to_message_id: str
    verdict: str = ""
    confidence: float | None = None
    reason: str = ""
    excerpt: str = ""
    # Set on SENT: the id of the message this program created, so it can be found later.
    posted_message_id: str | None = None
    # Set on FAILED: why.
    error: str | None = None
