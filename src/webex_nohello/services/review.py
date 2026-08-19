"""Collecting past messages to judge the classifier against.

This is an evaluation aid, not part of a run. It exists because `run` deliberately shows the
classifier only the newest message in each space and only once, which is correct for
production and useless for measuring: after one run, everything is behind a high-water mark.

So this ignores marks entirely, walks a window of history, and builds one candidate per
inbound message — each with exactly the context production would have given it, namely the
messages that preceded it and nothing after. It never writes state and cannot post.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from itertools import islice

from webex_nohello.clock import Clock
from webex_nohello.models.errors.webex_api_error import WebexApiError
from webex_nohello.models.review.historical_candidate import HistoricalCandidate
from webex_nohello.models.run.candidate import Candidate
from webex_nohello.models.webex.message import Message
from webex_nohello.models.webex.person import Person
from webex_nohello.models.webex.space import Space
from webex_nohello.services.scan import (
    DEFAULT_CONTEXT_MESSAGES,
    ScanProgress,
    SilentProgress,
    SpaceReader,
)

DEFAULT_LOOKBACK = timedelta(days=7)
# Every message costs a model call, so the default is a sample rather than everything.
DEFAULT_MAX_MESSAGES = 40


class ReviewService:
    def __init__(
        self,
        reader: SpaceReader,
        clock: Clock,
        *,
        context_messages: int = DEFAULT_CONTEXT_MESSAGES,
    ) -> None:
        self._reader = reader
        self._clock = clock
        self._context_messages = context_messages

    def collect(
        self,
        operator: Person,
        *,
        lookback: timedelta = DEFAULT_LOOKBACK,
        max_spaces: int | None = None,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        progress: ScanProgress | None = None,
    ) -> tuple[HistoricalCandidate, ...]:
        since = self._clock() - lookback
        watcher = progress if progress is not None else SilentProgress()

        watcher.listing_spaces()
        spaces: Iterator[Space] = self._reader.iter_direct_spaces()
        if max_spaces is not None:
            spaces = islice(spaces, max_spaces)

        collected: list[HistoricalCandidate] = []
        for space in spaces:
            if space.last_activity is not None and space.last_activity < since:
                # Ordered newest-first, so nothing later can be in the window either.
                watcher.stopped_at_cutoff()
                break

            watcher.examining(space)
            messages = self._reader.recent_messages(space.id, limit=self._context_messages)
            oldest_first = tuple(reversed(messages))

            for candidate in self._candidates_in(space, oldest_first, operator, since):
                collected.append(candidate)
                watcher.candidate_found(space)
                if len(collected) >= max_messages:
                    return tuple(collected)

        return tuple(collected)

    def _candidates_in(
        self,
        space: Space,
        oldest_first: tuple[Message, ...],
        operator: Person,
        since: datetime,
    ) -> list[HistoricalCandidate]:
        found: list[HistoricalCandidate] = []
        for index, message in enumerate(oldest_first):
            if not self._is_worth_judging(message, operator, since):
                continue
            found.append(
                HistoricalCandidate(
                    candidate=Candidate(
                        space=space,
                        message=message,
                        # Only what preceded it: showing the classifier the reply it prompted
                        # would leak the answer and make the evaluation meaningless.
                        conversation=oldest_first[: index + 1],
                    ),
                    superseded_after=_gap_to_next(oldest_first, index),
                )
            )
        return found

    def _is_worth_judging(self, message: Message, operator: Person, since: datetime) -> bool:
        if message.person_id == operator.id or not message.has_text:
            return False
        if message.created is not None and message.created < since:
            return False
        return self._is_from_a_human(message)

    def _is_from_a_human(self, message: Message) -> bool:
        if not message.person_id:
            return False
        try:
            return self._reader.get_person(message.person_id).is_human
        except WebexApiError:
            return False


def _gap_to_next(oldest_first: tuple[Message, ...], index: int) -> timedelta | None:
    """How long this message stayed the newest in its space, or None if it still is.

    Any later message supersedes it, whoever sent it: a later message from them makes theirs
    the candidate instead, and a later message from the operator means the space is skipped
    outright under Article VI.3.
    """
    if index + 1 >= len(oldest_first):
        return None

    this, following = oldest_first[index], oldest_first[index + 1]
    if this.created is None or following.created is None:
        return None
    return following.created - this.created
