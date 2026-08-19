"""What happened to one candidate at the sending stage, and to the run as a whole."""

from __future__ import annotations

from dataclasses import dataclass, field

from webex_nohello.models.classify.assessment import Assessment
from webex_nohello.models.reply.withheld_reason import WithheldReason


@dataclass(frozen=True)
class ReplyOutcome:
    assessment: Assessment
    # Exactly one of these is set: either it went, or a rail stopped it.
    sent_message_id: str | None = None
    withheld: WithheldReason | None = None
    # A send that was attempted and definitely failed. The audit record still stands, so
    # this person will not be tried again — silence beats a possible duplicate.
    error: str | None = None

    @property
    def was_sent(self) -> bool:
        return self.sent_message_id is not None


@dataclass(frozen=True)
class DispatchResult:
    outcomes: tuple[ReplyOutcome, ...] = field(default_factory=tuple)
    # True when the run wanted to send more than the cap allowed. Article X.5 treats this as
    # a fault to report loudly, not a limit to quietly apply.
    hit_run_cap: bool = False

    @property
    def sent(self) -> tuple[ReplyOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.was_sent)

    @property
    def failed(self) -> tuple[ReplyOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.error is not None)
