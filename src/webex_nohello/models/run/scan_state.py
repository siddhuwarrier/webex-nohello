"""Everything the program remembers between runs.

Versioned, because Article VI.6 requires the format be recognisable: an unreadable state
file must fail loudly rather than be silently treated as "never run", which would
re-examine every message ever sent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from webex_nohello.models.run.high_water_mark import HighWaterMark

SCAN_STATE_VERSION: Literal[1] = 1


class ScanState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = SCAN_STATE_VERSION
    marks: dict[str, HighWaterMark] = Field(default_factory=dict)
    # The highest `lastActivity` seen across all spaces on the previous scan. Because
    # `rooms.list` is sorted by that field descending, any space older than this cannot
    # have changed, and neither can any space after it — so the scan stops there instead
    # of reading every space every time. Deliberately a Webex-supplied timestamp rather
    # than a local clock reading, so clock skew cannot cause a space to be missed.
    last_activity_seen: datetime | None = None

    @property
    def is_first_run(self) -> bool:
        """No space has ever been read. Article VI.4 forbids replying to anything."""
        return not self.marks

    def mark_for(self, space_id: str) -> HighWaterMark | None:
        return self.marks.get(space_id)

    def with_mark(self, mark: HighWaterMark) -> ScanState:
        return self.model_copy(update={"marks": {**self.marks, mark.space_id: mark}})

    def with_last_activity_seen(self, seen: datetime | None) -> ScanState:
        return self.model_copy(update={"last_activity_seen": seen})
