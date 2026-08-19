"""A past message, judged as though it were the newest — and whether it still is.

`review` evaluates every message with only what preceded it, which answers "what would the
classifier have said at that moment". That is not the same as "would a reply have been
sent", because Article VI.2 only ever treats the *newest* message in a space as a candidate.

A greeting followed four minutes later by a real question is never replied to by a scheduled
run: by the time it looks, the question is the newest message and the greeting is context.
So a flagged message is only a real misfire risk if the poll would have landed in the gap
before the next message arrived. `superseded_after` is that gap, and reporting without it
overstates the risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from webex_nohello.models.run.candidate import Candidate


@dataclass(frozen=True)
class HistoricalCandidate:
    candidate: Candidate
    # How long this message stayed the newest in its space. None means it still is, so a run
    # right now would judge it for real.
    superseded_after: timedelta | None = None

    @property
    def is_still_current(self) -> bool:
        return self.superseded_after is None

    def would_be_seen_by_a_poll_every(self, interval: timedelta) -> bool:
        """Whether a poll at this interval could have caught it while it was still newest.

        Optimistic on purpose: it asks whether a poll *could* have landed in the window, not
        whether one would have. Being optimistic here means over-reporting risk rather than
        under-reporting it, which is the safe direction for a program that posts as you.
        """
        if self.superseded_after is None:
            return True
        return self.superseded_after >= interval
