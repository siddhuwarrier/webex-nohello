"""One candidate, classified.

Exactly one of `verdict` and `failure` is set. A failure is a normal outcome, not an
error: Article V.4 requires an unparseable answer to cost that one candidate its
classification rather than abandoning the run.
"""

from __future__ import annotations

from dataclasses import dataclass

from webex_nohello.models.classify.verdict import Verdict
from webex_nohello.models.run.candidate import Candidate


@dataclass(frozen=True)
class Assessment:
    candidate: Candidate
    verdict: Verdict | None = None
    failure: str | None = None
    # Decided by the service, which owns the confidence threshold. Held as a value rather
    # than recomputed, so the audit log and the dry-run report cannot disagree.
    is_reply_warranted: bool = False
    # True when a cheap local check ruled the message out without paying for inference.
    was_prefiltered: bool = False

    @property
    def summary(self) -> str:
        """One line for the report, whichever way it went."""
        if self.failure is not None:
            return f"could not classify: {self.failure}"
        if self.verdict is None:
            return "not classified"
        return f"{self.verdict.verdict} ({self.verdict.confidence:.2f}) — {self.verdict.reason}"
