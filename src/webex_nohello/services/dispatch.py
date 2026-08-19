"""Deciding what actually gets sent, and sending it.

Every rail in Article X lives here, and they are applied in one place so that no caller can
skip one by forgetting. The order is cheapest and most absolute first: who may be written to
at all, then whether they were written to recently, then the per-run cap, then finally
whether this run is even allowed to send.

The sequence around a send is the part to read carefully:

    1. write the ATTEMPTED record, flushed and fsynced
    2. post the reply
    3. write SENT or FAILED

Step 1 before step 2 is deliberate and is what Article X.7 requires. A crash between them
costs one reply that never arrives. The other order would risk a colleague receiving the same
automated message twice, which is far worse than receiving it never.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol

from webex_nohello.clock import Clock
from webex_nohello.models.audit.reply_record import ReplyEvent, ReplyRecord
from webex_nohello.models.classify.assessment import Assessment
from webex_nohello.models.config.settings import Settings
from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError
from webex_nohello.models.reply.dispatch_outcome import DispatchResult, ReplyOutcome
from webex_nohello.models.reply.withheld_reason import WithheldReason
from webex_nohello.models.webex.message import Message
from webex_nohello.models.webex.person import Person
from webex_nohello.services.audit import AuditLog, is_in_cooldown
from webex_nohello.services.reply_template import render


class ReplyPoster(Protocol):
    """The single write this service needs. Declared here, per dependency inversion."""

    def post_thread_reply(self, space_id: str, parent_id: str, markdown: str) -> Message: ...


class DispatchService:
    def __init__(
        self,
        webex: ReplyPoster,
        audit: AuditLog,
        settings: Settings,
        clock: Clock,
        *,
        template: str,
    ) -> None:
        self._webex = webex
        self._audit = audit
        self._settings = settings
        self._clock = clock
        self._template = template

    def dispatch(self, assessments: list[Assessment], *, commit: bool) -> DispatchResult:
        outcomes: list[ReplyOutcome] = []
        sent = 0
        wanted_more_than_the_cap = False

        for assessment in assessments:
            if not assessment.is_reply_warranted:
                continue

            withheld = self._first_rail_to_object(assessment, already_sent=sent, commit=commit)
            if withheld is WithheldReason.OVER_RUN_CAP:
                wanted_more_than_the_cap = True
            if withheld is not None:
                outcomes.append(ReplyOutcome(assessment=assessment, withheld=withheld))
                continue

            outcome = self._send(assessment)
            outcomes.append(outcome)
            if outcome.was_sent or outcome.error is not None:
                # A failed send still consumed the attempt, so it counts against the cap.
                sent += 1

        return DispatchResult(outcomes=tuple(outcomes), hit_run_cap=wanted_more_than_the_cap)

    def _first_rail_to_object(
        self, assessment: Assessment, *, already_sent: int, commit: bool
    ) -> WithheldReason | None:
        recipient = assessment.candidate.sender_email

        if not self._settings.is_addressable(recipient):
            return WithheldReason.NOT_ADDRESSABLE

        if is_in_cooldown(
            self._audit.last_attempt_to(recipient),
            self._clock(),
            timedelta(minutes=self._settings.cooldown_minutes),
        ):
            return WithheldReason.IN_COOLDOWN

        if already_sent >= self._settings.max_replies_per_run:
            return WithheldReason.OVER_RUN_CAP

        # Checked last so that a dry run still reports every other rail it would have hit,
        # which is what makes a dry run worth reading.
        if not commit:
            return WithheldReason.DRY_RUN

        return None

    def preview(self, assessment: Assessment) -> str:
        """The exact text that would be posted. Article X.2 requires a dry run show this."""
        return render(self._template, _sender_of(assessment))

    def _send(self, assessment: Assessment) -> ReplyOutcome:
        candidate = assessment.candidate
        sender = _sender_of(assessment)
        body = render(self._template, sender)

        common = {
            "at": self._clock(),
            "space_id": candidate.space.id,
            "recipient_email": candidate.sender_email,
            "replied_to_message_id": candidate.message.id,
            "verdict": assessment.verdict.verdict.value if assessment.verdict else "",
            "confidence": assessment.verdict.confidence if assessment.verdict else None,
            "reason": assessment.verdict.reason if assessment.verdict else "",
            "excerpt": candidate.message.excerpt(),
        }

        # Durable before the send. If this raises, nothing is posted: a reply that cannot be
        # recorded must not be sent, because the record is what prevents a second one.
        self._audit.record(ReplyRecord(event=ReplyEvent.ATTEMPTED, **common))

        try:
            posted = self._webex.post_thread_reply(candidate.space.id, candidate.message.id, body)
        except WebexNoHelloError as exc:
            self._audit.record(ReplyRecord(event=ReplyEvent.FAILED, error=exc.message, **common))
            return ReplyOutcome(assessment=assessment, error=exc.message)

        self._audit.record(
            ReplyRecord(event=ReplyEvent.SENT, posted_message_id=posted.id, **common)
        )
        return ReplyOutcome(assessment=assessment, sent_message_id=posted.id)


def _sender_of(assessment: Assessment) -> Person:
    """The person being replied to, as far as the message itself reveals.

    Built from the message rather than fetched: the scan already resolved this author to
    confirm they are human, and the template needs only a name and an address.
    """
    message = assessment.candidate.message
    return Person(
        id=message.person_id,
        emails=[message.person_email] if message.person_email else [],
        display_name=assessment.candidate.space.title,
    )
