"""ReviewService: which historical messages get judged, and with what context.

The property that makes an evaluation meaningful is that each message is shown only what
preceded it. Including what came after would leak the answer — the operator's own reply is
the strongest possible hint about whether a message carried a request.
"""

from __future__ import annotations

from datetime import timedelta

from conftest import (
    NOW,
    FakeSpaceReader,
    clock_at,
    make_message,
    make_person,
    make_space,
)
from webex_nohello.services.review import ReviewService

OPERATOR = make_person(person_id="me", email="me@example.com")
SENDER = make_person(person_id="them", email="them@example.com")
BOT = make_person(person_id="bot", email="thing@webex.bot", person_type="bot")


def build(reader: FakeSpaceReader, context: int = 10) -> ReviewService:
    return ReviewService(reader, clock_at(), context_messages=context)


class TestWhichMessagesAreJudged:
    def test_every_inbound_message_becomes_a_candidate(self) -> None:
        """Unlike a run, which only ever looks at the newest."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader(
            {
                space: [
                    make_message("m3", sender=SENDER, text="third"),
                    make_message("m2", sender=SENDER, text="second"),
                    make_message("m1", sender=SENDER, text="first"),
                ]
            },
            [SENDER],
        )

        collected = build(reader).collect(OPERATOR)

        assert [one.candidate.message.id for one in collected] == ["m1", "m2", "m3"]

    def test_my_own_messages_are_not_judged(self) -> None:
        space = make_space("s1", "Them")
        reader = FakeSpaceReader(
            {
                space: [
                    make_message("m2", sender=OPERATOR, text="mine"),
                    make_message("m1", sender=SENDER, text="theirs"),
                ]
            },
            [SENDER],
        )

        collected = build(reader).collect(OPERATOR)

        assert [one.candidate.message.id for one in collected] == ["m1"]

    def test_messages_without_text_are_not_judged(self) -> None:
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="")]}, [SENDER])

        assert build(reader).collect(OPERATOR) == ()

    def test_bot_messages_are_not_judged(self) -> None:
        space = make_space("s1", "Bot")
        reader = FakeSpaceReader({space: [make_message("m1", sender=BOT, text="hi")]}, [BOT])

        assert build(reader).collect(OPERATOR) == ()

    def test_messages_older_than_the_window_are_not_judged(self) -> None:
        space = make_space("s1", "Them")
        reader = FakeSpaceReader(
            {
                space: [
                    make_message("m2", sender=SENDER, text="recent", created=NOW),
                    make_message(
                        "m1", sender=SENDER, text="ancient", created=NOW - timedelta(days=30)
                    ),
                ]
            },
            [SENDER],
        )

        collected = build(reader).collect(OPERATOR, lookback=timedelta(days=7))

        assert [one.candidate.message.id for one in collected] == ["m2"]


class TestContext:
    def test_a_message_is_shown_only_what_preceded_it(self) -> None:
        """The guard against leaking the answer into the evaluation."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader(
            {
                space: [
                    make_message("m3", sender=SENDER, text="third"),
                    make_message("m2", sender=OPERATOR, text="my answer"),
                    make_message("m1", sender=SENDER, text="first"),
                ]
            },
            [SENDER],
        )

        collected = build(reader).collect(OPERATOR)

        first = next(one for one in collected if one.candidate.message.id == "m1")
        assert [message.id for message in first.candidate.conversation] == ["m1"]

        third = next(one for one in collected if one.candidate.message.id == "m3")
        assert [message.id for message in third.candidate.conversation] == ["m1", "m2", "m3"]

    def test_context_is_oldest_first_and_ends_with_the_message(self) -> None:
        space = make_space("s1", "Them")
        reader = FakeSpaceReader(
            {
                space: [
                    make_message("m2", sender=SENDER, text="later"),
                    make_message("m1", sender=SENDER, text="earlier"),
                ]
            },
            [SENDER],
        )

        collected = build(reader).collect(OPERATOR)

        latest = next(one for one in collected if one.candidate.message.id == "m2")
        assert latest.candidate.conversation[0].id == "m1"
        assert latest.candidate.conversation[-1].id == latest.candidate.message.id


class TestSupersession:
    """A greeting overtaken by a later message is never replied to (Article VI.2).

    Without this, `review` overstates the risk: it judges every message as though the tool
    had run at that instant, which is not how a scheduled run behaves.
    """

    def test_a_greeting_followed_by_a_request_is_marked_superseded(self) -> None:
        space = make_space("s1", "Them")
        reader = FakeSpaceReader(
            {
                space: [
                    make_message(
                        "m2",
                        sender=SENDER,
                        text="can you look at the staging failure?",
                        created=NOW,
                    ),
                    make_message(
                        "m1", sender=SENDER, text="hi", created=NOW - timedelta(minutes=4)
                    ),
                ]
            },
            [SENDER],
        )

        collected = build(reader).collect(OPERATOR)

        greeting = next(one for one in collected if one.candidate.message.id == "m1")
        assert greeting.superseded_after == timedelta(minutes=4)
        assert not greeting.is_still_current

    def test_the_newest_message_is_still_current(self) -> None:
        space = make_space("s1", "Them")
        reader = FakeSpaceReader(
            {
                space: [
                    make_message("m2", sender=SENDER, text="second", created=NOW),
                    make_message(
                        "m1", sender=SENDER, text="first", created=NOW - timedelta(minutes=4)
                    ),
                ]
            },
            [SENDER],
        )

        collected = build(reader).collect(OPERATOR)

        newest = next(one for one in collected if one.candidate.message.id == "m2")
        assert newest.superseded_after is None
        assert newest.is_still_current

    def test_a_reply_of_my_own_also_supersedes(self) -> None:
        """Article VI.3 skips the space outright once I have engaged."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader(
            {
                space: [
                    make_message("m2", sender=OPERATOR, text="on it", created=NOW),
                    make_message(
                        "m1", sender=SENDER, text="hi", created=NOW - timedelta(minutes=2)
                    ),
                ]
            },
            [SENDER],
        )

        collected = build(reader).collect(OPERATOR)

        greeting = next(one for one in collected if one.candidate.message.id == "m1")
        assert greeting.superseded_after == timedelta(minutes=2)

    def test_a_poll_slower_than_the_gap_would_never_have_seen_it(self) -> None:
        """The answer to "would a run at 10:35 reply to the 10:30 greeting?" -- no."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader(
            {
                space: [
                    make_message("m2", sender=SENDER, text="a real question?", created=NOW),
                    make_message(
                        "m1", sender=SENDER, text="hi", created=NOW - timedelta(minutes=4)
                    ),
                ]
            },
            [SENDER],
        )

        greeting = next(
            one for one in build(reader).collect(OPERATOR) if one.candidate.message.id == "m1"
        )

        assert not greeting.would_be_seen_by_a_poll_every(timedelta(minutes=15))
        assert greeting.would_be_seen_by_a_poll_every(timedelta(minutes=2))

    def test_a_still_current_message_is_reachable_at_any_interval(self) -> None:
        space = make_space("s1", "Them")
        reader = FakeSpaceReader({space: [make_message("m1", sender=SENDER, text="hi")]}, [SENDER])

        only = build(reader).collect(OPERATOR)[0]

        assert only.would_be_seen_by_a_poll_every(timedelta(hours=24))


class TestBounds:
    def test_collection_stops_at_the_message_cap(self) -> None:
        """Every message costs a model call, so an unbounded review could cost real money."""
        space = make_space("s1", "Them")
        reader = FakeSpaceReader(
            {
                space: [
                    make_message(f"m{index}", sender=SENDER, text=f"message {index}")
                    for index in range(10, 0, -1)
                ]
            },
            [SENDER],
        )

        collected = build(reader).collect(OPERATOR, max_messages=3)

        assert len(collected) == 3

    def test_spaces_outside_the_window_are_never_opened(self) -> None:
        recent = make_space("s1", "Recent", last_activity=NOW)
        ancient = make_space("s2", "Ancient", last_activity=NOW - timedelta(days=60))
        reader = FakeSpaceReader(
            {
                recent: [make_message("m1", sender=SENDER, text="hi")],
                ancient: [make_message("m2", sender=SENDER, text="hi")],
            },
            [SENDER],
        )

        build(reader).collect(OPERATOR, lookback=timedelta(days=7))

        assert reader.opened_spaces == ["s1"]

    def test_the_space_cap_is_honoured(self) -> None:
        first = make_space("s1", "A", last_activity=NOW)
        second = make_space("s2", "B", last_activity=NOW)
        reader = FakeSpaceReader(
            {
                first: [make_message("m1", sender=SENDER, text="hi")],
                second: [make_message("m2", sender=SENDER, text="hi")],
            },
            [SENDER],
        )

        build(reader).collect(OPERATOR, max_spaces=1)

        assert reader.opened_spaces == ["s1"]
