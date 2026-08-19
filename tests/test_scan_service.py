"""ScanService: which messages become candidates, and why the rest do not.

This is Article VI. The decisive property is that a message is examined exactly once,
because in a committing run a second look means a second reply.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import (
    NOW,
    FakeSpaceReader,
    InMemoryStateStore,
    RecordingProgress,
    clock_at,
    make_message,
    make_person,
    make_space,
    make_state,
)
from webex_nohello.models.run.skip_reason import SkipReason
from webex_nohello.services.scan import DEFAULT_LOOKBACK, ScanService

OPERATOR = make_person(person_id="me", email="me@example.com")
SENDER = make_person(person_id="them", email="them@example.com")


def build_scanner(
    reader: FakeSpaceReader, store: InMemoryStateStore | None = None, context: int = 10
) -> ScanService:
    return ScanService(
        reader,
        store if store is not None else InMemoryStateStore(),
        clock_at(),
        context_messages=context,
    )


class TestCandidateSelection:
    def test_a_lone_greeting_from_someone_else_is_a_candidate(self) -> None:
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])

        result = build_scanner(reader).scan(OPERATOR)

        assert len(result.candidates) == 1
        candidate = result.candidates[0]
        assert candidate.message.id == "m1"
        assert candidate.space.id == "s1"
        assert candidate.is_first_contact

    def test_the_conversation_is_supplied_oldest_first(self) -> None:
        """Article IX.2: the classifier reads it in the order a human would."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader(
            {
                space: [
                    make_message("m3", sender=SENDER, text="lol", created=NOW),
                    make_message("m2", sender=OPERATOR, text="then it shipped", created=NOW),
                    make_message("m1", sender=SENDER, text="how did it go", created=NOW),
                ]
            },
            [SENDER],
        )

        result = build_scanner(reader).scan(OPERATOR)

        conversation = result.candidates[0].conversation
        assert [message.id for message in conversation] == ["m1", "m2", "m3"]
        assert conversation[-1].id == result.candidates[0].message.id
        assert not result.candidates[0].is_first_contact

    def test_several_spaces_each_yield_their_own_candidate(self) -> None:
        first, second = make_space("s1", "A"), make_space("s2", "B")
        reader = FakeSpaceReader(
            {
                first: [make_message("m1", sender=SENDER, text="hi")],
                second: [make_message("m2", sender=SENDER, text="hello")],
            },
            [SENDER],
        )

        result = build_scanner(reader).scan(OPERATOR)

        assert {candidate.message.id for candidate in result.candidates} == {"m1", "m2"}
        assert result.spaces_examined == 2


class TestSkipping:
    def _skip_reason(
        self, reader: FakeSpaceReader, store: InMemoryStateStore | None = None
    ) -> SkipReason:
        result = build_scanner(reader, store).scan(OPERATOR)
        assert result.candidates == ()
        assert len(result.skipped) == 1
        return result.skipped[0].reason

    def test_an_empty_space_is_skipped(self) -> None:
        reader = FakeSpaceReader({make_space("s1", "Them"): []}, [SENDER])

        assert self._skip_reason(reader) is SkipReason.NO_MESSAGES

    def test_a_message_already_marked_is_not_examined_again(self) -> None:
        """Article VI.5, and the reason a repeated run does not re-reply."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])
        store = InMemoryStateStore(
            make_state({"s1": "m1"}, last_activity_seen=NOW - timedelta(days=1))
        )

        assert self._skip_reason(reader, store) is SkipReason.NOTHING_NEW

    def test_a_newer_message_after_a_mark_is_a_candidate_again(self) -> None:
        space = make_space("s1", "Them")
        reader = FakeSpaceReader(
            {
                space: [
                    make_message("m2", sender=SENDER, text="hi again"),
                    make_message("m1", sender=SENDER, text="hi"),
                ]
            },
            [SENDER],
        )
        store = InMemoryStateStore(
            make_state({"s1": "m1"}, last_activity_seen=NOW - timedelta(days=1))
        )

        result = build_scanner(reader, store).scan(OPERATOR)

        assert [candidate.message.id for candidate in result.candidates] == ["m2"]

    def test_my_own_message_is_never_a_candidate(self) -> None:
        """Article I.2."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=OPERATOR, text="hi")]}, [])

        assert self._skip_reason(reader) is SkipReason.LATEST_IS_MINE

    def test_a_space_where_i_replied_last_is_skipped(self) -> None:
        """Article VI.3: I have already engaged, whatever the classifier would say."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader(
            {
                space: [
                    make_message("m2", sender=OPERATOR, text="on it"),
                    make_message("m1", sender=SENDER, text="hi"),
                ]
            },
            [SENDER],
        )

        assert self._skip_reason(reader) is SkipReason.LATEST_IS_MINE

    def test_a_message_with_no_text_is_skipped(self) -> None:
        """An attachment or a card is not a greeting."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="")]}, [SENDER])

        assert self._skip_reason(reader) is SkipReason.NO_TEXT

    def test_whitespace_only_text_counts_as_no_text(self) -> None:
        space = make_space("s1", "Them")
        reader = FakeSpaceReader(
            {space: [make_message("m1", sender=SENDER, text="   \n")]}, [SENDER]
        )

        assert self._skip_reason(reader) is SkipReason.NO_TEXT

    def test_a_bot_is_never_replied_to(self) -> None:
        """Article I.3."""
        bot = make_person(person_id="bot", email="thing@webex.bot", person_type="bot")
        space = make_space("s1", "Thing")
        reader = FakeSpaceReader({space: [make_message("m1", sender=bot, text="hi")]}, [bot])

        assert self._skip_reason(reader) is SkipReason.SENDER_IS_NOT_HUMAN

    def test_an_unresolvable_author_is_not_replied_to(self) -> None:
        """Fails closed, per Article VII.2."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [])

        assert self._skip_reason(reader) is SkipReason.SENDER_IS_NOT_HUMAN

    def test_the_bot_lookup_is_avoided_when_a_cheaper_check_already_decided(self) -> None:
        """The lookup is an API call; a poll every few minutes must not make it needlessly."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=OPERATOR, text="hi")]}, [])

        build_scanner(reader).scan(OPERATOR)

        assert reader.person_lookups == []


class TestProposedMarks:
    def test_the_mark_advances_for_a_candidate(self) -> None:
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])

        result = build_scanner(reader).scan(OPERATOR)

        assert result.proposed_state.marks["s1"].message_id == "m1"

    def test_the_mark_advances_even_for_a_skipped_space(self) -> None:
        """Article VI.5: otherwise a message judged fine would be re-examined forever."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=OPERATOR, text="hi")]}, [])

        result = build_scanner(reader).scan(OPERATOR)

        assert result.proposed_state.marks["s1"].message_id == "m1"

    def test_no_mark_is_proposed_for_an_empty_space(self) -> None:
        reader = FakeSpaceReader({make_space("s1", "Them"): []}, [SENDER])

        result = build_scanner(reader).scan(OPERATOR)

        assert "s1" not in result.proposed_state.marks

    def test_the_scan_does_not_persist_anything(self) -> None:
        """Article X.2: advancing marks is the caller's decision, not the scan's."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])
        store = InMemoryStateStore()

        build_scanner(reader, store).scan(OPERATOR)

        assert store.load().marks == {}

    def test_marks_for_spaces_not_seen_this_run_are_preserved(self) -> None:
        """A space with no recent activity must not lose its position."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])
        store = InMemoryStateStore(
            make_state({"s9": "old"}, last_activity_seen=NOW - timedelta(days=1))
        )

        result = build_scanner(reader, store).scan(OPERATOR)

        assert result.proposed_state.marks["s9"].message_id == "old"


class TestFirstRun:
    def test_an_empty_state_is_a_first_run(self) -> None:
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])

        result = build_scanner(reader).scan(OPERATOR)

        assert result.is_first_run

    def test_a_first_run_still_reports_what_it_would_have_considered(self) -> None:
        """Article VI.4 requires the report, so the operator can see what was in scope."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])

        result = build_scanner(reader).scan(OPERATOR)

        assert len(result.candidates) == 1

    def test_a_populated_state_is_not_a_first_run(self) -> None:
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m2", sender=SENDER, text="hi")]}, [SENDER])
        store = InMemoryStateStore(
            make_state({"s1": "m1"}, last_activity_seen=NOW - timedelta(days=1))
        )

        result = build_scanner(reader, store).scan(OPERATOR)

        assert not result.is_first_run


class TestReadingBudget:
    """The scan must not cost one request per space on every run; there is no unread API."""

    def test_the_space_cap_stops_the_listing_rather_than_trimming_it(self) -> None:
        """The SDK's `max` is a page size, not a total, so the cap must be applied here."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])

        build_scanner(reader).scan(OPERATOR, max_spaces=5)

        assert reader.listed_spaces == ["s1"]

    def test_a_space_costs_exactly_one_request(self) -> None:
        """The SDK paginates per request, so request count is what governs the wall clock."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])

        build_scanner(reader, context=8).scan(OPERATOR)

        assert reader.message_limits == [8]

    def test_a_space_yielding_no_candidate_also_costs_one_request(self) -> None:
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=OPERATOR, text="hi")]}, [])

        build_scanner(reader, context=8).scan(OPERATOR)

        assert reader.message_limits == [8]

    def test_spaces_older_than_the_recorded_activity_are_never_opened(self) -> None:
        """The whole optimisation: `rooms.list` is ordered, so the scan can stop early."""
        recent = make_space("s1", "Recent", last_activity=NOW)
        stale = make_space("s2", "Stale", last_activity=NOW - timedelta(days=2))
        older = make_space("s3", "Older", last_activity=NOW - timedelta(days=5))
        reader = FakeSpaceReader(
            {
                recent: [make_message("m2", sender=SENDER, text="hi", created=NOW)],
                stale: [make_message("m1", sender=SENDER, text="hi")],
                older: [make_message("m0", sender=SENDER, text="hi")],
            },
            [SENDER],
        )
        store = InMemoryStateStore(
            make_state({"s1": "m1"}, last_activity_seen=NOW - timedelta(days=1))
        )

        result = build_scanner(reader, store).scan(OPERATOR)

        assert result.spaces_examined == 1
        assert result.stopped_at_cutoff
        assert reader.opened_spaces == ["s1"]

    def test_the_cutoff_advances_to_the_newest_activity_seen(self) -> None:
        newest = NOW + timedelta(hours=1)
        space = make_space("s1", "Them", last_activity=newest)
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])

        result = build_scanner(reader).scan(OPERATOR)

        assert result.proposed_state.last_activity_seen == newest

    def test_the_cutoff_never_moves_backwards(self) -> None:
        """A quiet run must not widen the window that the next run has to read."""
        space = make_space("s1", "Them", last_activity=NOW - timedelta(days=10))
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])
        store = InMemoryStateStore(make_state({}, last_activity_seen=NOW))

        result = build_scanner(reader, store).scan(OPERATOR)

        assert result.proposed_state.last_activity_seen == NOW

    def test_ignore_cutoff_reads_everything(self) -> None:
        """`--full`, for when the recorded position is suspected wrong."""
        stale = make_space("s1", "Stale", last_activity=NOW - timedelta(days=30))
        reader = FakeSpaceReader({stale: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])
        store = InMemoryStateStore(make_state({}, last_activity_seen=NOW))

        result = build_scanner(reader, store).scan(OPERATOR, lookback=timedelta(days=90))

        assert result.spaces_examined == 1
        assert reader.opened_spaces == ["s1"]

    def test_a_first_run_is_bounded_and_never_reads_everything(self) -> None:
        """There is no unread API, so an unbounded first run would open every space ever."""
        recent = make_space("s1", "A", last_activity=NOW - timedelta(days=1))
        ancient = make_space("s2", "B", last_activity=NOW - timedelta(days=100))
        reader = FakeSpaceReader(
            {
                recent: [make_message("m1", sender=SENDER, text="hi")],
                ancient: [make_message("m2", sender=SENDER, text="hello")],
            },
            [SENDER],
        )

        result = build_scanner(reader).scan(OPERATOR)

        assert result.is_first_run
        assert result.spaces_examined == 1
        assert result.stopped_at_cutoff
        assert reader.opened_spaces == ["s1"]

    def test_the_first_run_window_is_the_documented_default(self) -> None:
        just_inside = make_space(
            "s1", "A", last_activity=NOW - DEFAULT_LOOKBACK + timedelta(hours=1)
        )
        just_outside = make_space(
            "s2", "B", last_activity=NOW - DEFAULT_LOOKBACK - timedelta(hours=1)
        )
        reader = FakeSpaceReader(
            {
                just_inside: [make_message("m1", sender=SENDER, text="hi")],
                just_outside: [make_message("m2", sender=SENDER, text="hello")],
            },
            [SENDER],
        )

        result = build_scanner(reader).scan(OPERATOR)

        assert reader.opened_spaces == ["s1"]
        assert result.stopped_at_cutoff

    def test_an_explicit_lookback_overrides_a_recorded_position(self) -> None:
        """So an operator can deliberately re-examine a window without deleting state."""
        space = make_space("s1", "Them", last_activity=NOW - timedelta(days=30))
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])
        store = InMemoryStateStore(make_state({}, last_activity_seen=NOW))

        result = build_scanner(reader, store).scan(OPERATOR, lookback=timedelta(days=60))

        assert result.spaces_examined == 1
        assert reader.opened_spaces == ["s1"]

    def test_a_space_with_no_activity_timestamp_is_still_examined(self) -> None:
        """Failing open here only costs a request; failing closed would miss a message."""
        space = make_space("s1", "Them", last_activity=None)
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])
        store = InMemoryStateStore(make_state({}, last_activity_seen=NOW))

        result = build_scanner(reader, store).scan(OPERATOR)

        assert result.spaces_examined == 1


class TestProgressReporting:
    def test_the_sequence_names_each_space_as_it_is_opened(self) -> None:
        """A stalled run must show where it stalled, so the space is named before the read."""
        first = make_space("s1", "A", last_activity=NOW)
        second = make_space("s2", "B", last_activity=NOW - timedelta(hours=1))
        reader = FakeSpaceReader(
            {
                first: [make_message("m1", sender=SENDER, text="hi")],
                second: [make_message("m2", sender=OPERATOR, text="mine")],
            },
            [SENDER],
        )
        progress = RecordingProgress()

        build_scanner(reader).scan(OPERATOR, progress=progress)

        assert progress.events == [
            "listing",
            "examining:s1",
            "found:s1",
            "examining:s2",
        ]

    def test_spaces_beyond_the_cutoff_are_never_announced(self) -> None:
        recent = make_space("s1", "A", last_activity=NOW)
        stale = make_space("s2", "B", last_activity=NOW - timedelta(days=30))
        reader = FakeSpaceReader(
            {
                recent: [make_message("m1", sender=SENDER, text="hi")],
                stale: [make_message("m2", sender=SENDER, text="hi")],
            },
            [SENDER],
        )
        store = InMemoryStateStore(make_state({}, last_activity_seen=NOW - timedelta(days=1)))
        progress = RecordingProgress()

        build_scanner(reader, store).scan(OPERATOR, progress=progress)

        assert "examining:s2" not in progress.events

    def test_a_scan_without_a_progress_reporter_works(self) -> None:
        """The scheduled path passes nothing, and must not need a null object supplied."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])

        result = build_scanner(reader).scan(OPERATOR)

        assert len(result.candidates) == 1


@pytest.mark.parametrize("reason", list(SkipReason))
def test_every_skip_reason_reads_as_a_sentence(reason: SkipReason) -> None:
    """These strings go straight into the report, so they must make sense unprefixed."""
    assert reason.description
    assert reason.description[0].islower()


def test_counts_by_reason_groups_the_skips() -> None:
    first, second, third = make_space("s1", "A"), make_space("s2", "B"), make_space("s3", "C")
    reader = FakeSpaceReader(
        {
            first: [],
            second: [],
            third: [make_message("m1", sender=OPERATOR, text="hi", created=NOW + timedelta(1))],
        },
        [],
    )

    result = build_scanner(reader).scan(OPERATOR)

    assert result.counts_by_reason() == {
        SkipReason.NO_MESSAGES: 2,
        SkipReason.LATEST_IS_MINE: 1,
    }
