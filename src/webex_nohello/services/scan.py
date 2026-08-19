"""Finding messages that might warrant a reply.

This is Article VI in code. Webex tells us nothing about what we have read, so a candidate
is worked out from a locally held high-water mark per space: the newest message, newer
than the mark, not written by the operator, from a human, with text in it.

Two things keep a run cheap, because a naive version costs one request per space every
time and there is no unread endpoint to ask instead:

  * `rooms.list` is sorted by `lastActivity` descending, so once a space is older than the
    highest activity seen last time, no later space can have changed either. The scan
    stops there. On a polling schedule that turns hundreds of requests into a handful.
  * Only one message is fetched per space to decide whether anything is new. The fuller
    conversation the classifier needs is fetched only for spaces that produce a candidate.

The scan proposes new marks but does not persist them. Article X.2 forbids a dry run from
advancing them, so that decision belongs to the caller.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from itertools import islice
from typing import Protocol

from webex_nohello.clock import Clock
from webex_nohello.models.errors.webex_api_error import WebexApiError
from webex_nohello.models.run.candidate import Candidate
from webex_nohello.models.run.high_water_mark import HighWaterMark
from webex_nohello.models.run.scan_result import ScanResult, SkippedSpace
from webex_nohello.models.run.scan_state import ScanState
from webex_nohello.models.run.skip_reason import SkipReason
from webex_nohello.models.webex.message import Message
from webex_nohello.models.webex.person import Person
from webex_nohello.models.webex.space import Space
from webex_nohello.services.state import StateStore

# Article IX.2: the classifier needs the surrounding exchange, not just the last line.
DEFAULT_CONTEXT_MESSAGES = 10

# How far back the very first scan looks. There is no unread endpoint, so without a bound
# the first run would open every space the operator has ever had a conversation in. A week
# is generous for the purpose: a greeting older than that has already been ignored, and
# replying to it would be stranger than staying quiet.
DEFAULT_LOOKBACK = timedelta(days=7)


class SpaceReader(Protocol):
    """What the scan needs of Webex. Declared here, per dependency inversion."""

    def iter_direct_spaces(self) -> Iterator[Space]: ...

    def recent_messages(self, space_id: str, limit: int) -> tuple[Message, ...]: ...

    def get_person(self, person_id: str) -> Person: ...


class ScanProgress(Protocol):
    """How the scan reports what it is doing, without knowing how it will be shown.

    A scan makes one network request per space it opens, so on a slow link it can be
    quiet for a while. Reporting through a callback keeps the drawing in the command
    layer, per Article III.5.
    """

    def listing_spaces(self) -> None: ...

    def examining(self, space: Space) -> None: ...

    def candidate_found(self, space: Space) -> None: ...

    def stopped_at_cutoff(self) -> None: ...


class SilentProgress:
    """No output. Used by a scheduled run, where nothing is watching the terminal."""

    def listing_spaces(self) -> None:
        """Nothing to draw."""

    def examining(self, space: Space) -> None:
        """Nothing to draw."""

    def candidate_found(self, space: Space) -> None:
        """Nothing to draw."""

    def stopped_at_cutoff(self) -> None:
        """Nothing to draw."""


class ScanService:
    def __init__(
        self,
        reader: SpaceReader,
        state: StateStore,
        clock: Clock,
        *,
        context_messages: int = DEFAULT_CONTEXT_MESSAGES,
    ) -> None:
        self._reader = reader
        self._state = state
        self._clock = clock
        self._context_messages = context_messages

    def scan(
        self,
        operator: Person,
        *,
        max_spaces: int | None = None,
        lookback: timedelta | None = None,
        progress: ScanProgress | None = None,
    ) -> ScanResult:
        watcher = progress if progress is not None else SilentProgress()
        state = self._state.load()
        cutoff = self._cutoff(state, lookback)

        watcher.listing_spaces()
        # Consumed lazily. Abandoning this iterator stops the SDK following further
        # `Link: rel="next"` pages, which is the whole point of the cutoff.
        spaces: Iterator[Space] = self._reader.iter_direct_spaces()
        if max_spaces is not None:
            spaces = islice(spaces, max_spaces)

        candidates: list[Candidate] = []
        skipped: list[SkippedSpace] = []
        proposed = state
        examined = 0
        newest_seen = state.last_activity_seen
        previous_activity: datetime | None = None
        out_of_order = 0
        stopped_at_cutoff = False

        for space in spaces:
            if _is_out_of_order(space, previous_activity):
                out_of_order += 1
            previous_activity = space.last_activity or previous_activity

            if _is_older_than_cutoff(space, cutoff):
                # Sorted descending, so everything remaining is older still.
                stopped_at_cutoff = True
                watcher.stopped_at_cutoff()
                break

            examined += 1
            newest_seen = _later_of(newest_seen, space.last_activity)
            watcher.examining(space)

            # One request per space, fetching the classifier's context up front. Reading a
            # single message first and the rest later would halve the payload but double
            # the requests for any space that produces a candidate, and it is the requests
            # that cost the time.
            messages = self._reader.recent_messages(space.id, limit=self._context_messages)
            if not messages:
                skipped.append(SkippedSpace(space=space, reason=SkipReason.NO_MESSAGES))
                continue

            latest = messages[0]  # Webex returns newest first.

            # Article VI.5: the mark advances for every message examined, including ones
            # deliberately left alone, so nothing is ever looked at twice.
            proposed = proposed.with_mark(
                HighWaterMark(space_id=space.id, message_id=latest.id, created=latest.created)
            )

            reason = self._reject(latest, state.mark_for(space.id), operator)
            if reason is not None:
                skipped.append(SkippedSpace(space=space, reason=reason))
                continue

            watcher.candidate_found(space)
            candidates.append(
                Candidate(
                    space=space,
                    message=latest,
                    # Oldest first, per Article IX.2.
                    conversation=tuple(reversed(messages)),
                )
            )

        return ScanResult(
            candidates=tuple(candidates),
            skipped=tuple(skipped),
            proposed_state=proposed.with_last_activity_seen(newest_seen),
            is_first_run=state.is_first_run,
            spaces_examined=examined,
            stopped_at_cutoff=stopped_at_cutoff,
            out_of_order_spaces=out_of_order,
        )

    def _cutoff(self, state: ScanState, lookback: timedelta | None) -> datetime:
        """The oldest activity worth opening a space for.

        An explicit lookback always wins, so an operator can deliberately re-examine a
        window. Otherwise the recorded position is used, and failing that a bounded
        window — never the whole history.
        """
        if lookback is not None:
            return self._clock() - lookback
        if state.last_activity_seen is not None:
            return state.last_activity_seen
        return self._clock() - DEFAULT_LOOKBACK

    def _reject(
        self, latest: Message, mark: HighWaterMark | None, operator: Person
    ) -> SkipReason | None:
        """Why this space yields no candidate, or None if it does.

        Ordered cheapest first: the bot lookup is an API call, so everything decidable
        from what we already hold is decided before reaching it.
        """
        if mark is not None and mark.message_id == latest.id:
            return SkipReason.NOTHING_NEW

        # Article VI.3. Checked by id rather than address because a person can have
        # several addresses but only one id.
        if latest.person_id and latest.person_id == operator.id:
            return SkipReason.LATEST_IS_MINE

        if not latest.has_text:
            return SkipReason.NO_TEXT

        if not self._is_from_a_human(latest):
            return SkipReason.SENDER_IS_NOT_HUMAN

        return None

    def _is_from_a_human(self, message: Message) -> bool:
        """Article I.3, failing closed: an author we cannot resolve is not replied to."""
        if not message.person_id:
            return False
        try:
            return self._reader.get_person(message.person_id).is_human
        except WebexApiError:
            # Preflight already proved the token works, so a failure here is specific to
            # this person. Skipping one candidate beats abandoning the whole run, and
            # skipping is the safe direction.
            return False


def _is_older_than_cutoff(space: Space, cutoff: datetime) -> bool:
    # A space with no timestamp is examined rather than skipped: failing open here costs
    # one request, whereas failing closed would silently miss a message.
    if space.last_activity is None:
        return False
    return space.last_activity < cutoff


def _later_of(current: datetime | None, candidate: datetime | None) -> datetime | None:
    """Accumulate the newest activity seen, so the position never moves backwards.

    A quiet run must not widen the window the next run has to read. The bounded first-run
    cutoff is deliberately never fed in here: it is a local clock reading, and recording it
    would mix clock domains with Webex's own timestamps.
    """
    if candidate is None:
        return current
    if current is None:
        return candidate
    return max(current, candidate)


def _is_out_of_order(space: Space, previous: datetime | None) -> bool:
    """Whether Webex broke the descending order the early stop depends on.

    Counted rather than acted upon. If `sortBy=lastactivity` ever stops being honoured the
    early stop would silently skip spaces, so this makes a wrong assumption visible instead
    of turning it into missed messages.
    """
    if previous is None or space.last_activity is None:
        return False
    return space.last_activity > previous
