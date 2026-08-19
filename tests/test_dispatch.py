"""DispatchService: every rail in Article X.

These are the tests that matter most in the project. Each one fails if its rail is removed,
and each rail exists because the failure it prevents is a real message to a real colleague
that cannot be unsent.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import (
    NOW,
    FakeAuditLog,
    FakeWebexPoster,
    clock_at,
    make_assessment,
    make_person,
)
from webex_nohello.models.audit.reply_record import ReplyEvent
from webex_nohello.models.classify.assessment import Assessment
from webex_nohello.models.config.settings import Settings
from webex_nohello.models.errors.webex_api_error import WebexApiError
from webex_nohello.models.reply.withheld_reason import WithheldReason
from webex_nohello.services.dispatch import DispatchService

SENDER = make_person(person_id="them", email="them@example.com")
TEMPLATE = "Automated reply. See https://nohello.net/en/"


def build(
    *,
    settings: Settings | None = None,
    audit: FakeAuditLog | None = None,
    webex: FakeWebexPoster | None = None,
) -> tuple[DispatchService, FakeWebexPoster, FakeAuditLog]:
    poster = webex if webex is not None else FakeWebexPoster()
    log = audit if audit is not None else FakeAuditLog()
    service = DispatchService(
        poster,
        log,
        settings if settings is not None else Settings(opt_in_only=False),
        clock_at(),
        template=TEMPLATE,
    )
    return service, poster, log


class TestDryRunIsTheDefault:
    def test_a_dry_run_posts_nothing(self) -> None:
        """Article X.1. The single most important default in the program."""
        service, poster, _ = build()

        result = service.dispatch([make_assessment(sender=SENDER)], commit=False)

        assert poster.posted == []
        assert result.outcomes[0].withheld is WithheldReason.DRY_RUN

    def test_a_dry_run_writes_no_audit_record(self) -> None:
        service, _, log = build()

        service.dispatch([make_assessment(sender=SENDER)], commit=False)

        assert log.records == []

    def test_committing_posts(self) -> None:
        service, poster, _ = build()

        result = service.dispatch([make_assessment(sender=SENDER)], commit=True)

        assert len(poster.posted) == 1
        assert result.sent
        assert result.outcomes[0].sent_message_id


class TestOptInOnly:
    def test_nobody_is_replied_to_by_default(self) -> None:
        """Article X.4, and the default: an unconfigured install replies to no one."""
        service, poster, _ = build(settings=Settings())

        result = service.dispatch([make_assessment(sender=SENDER)], commit=True)

        assert poster.posted == []
        assert result.outcomes[0].withheld is WithheldReason.NOT_ADDRESSABLE

    def test_someone_on_the_allow_list_is_replied_to(self) -> None:
        settings = Settings(opt_in_only=True, allow_list=("them@example.com",))
        service, poster, _ = build(settings=settings)

        service.dispatch([make_assessment(sender=SENDER)], commit=True)

        assert len(poster.posted) == 1

    def test_the_allow_list_is_case_insensitive(self) -> None:
        settings = Settings(opt_in_only=True, allow_list=("THEM@Example.COM",))
        service, poster, _ = build(settings=settings)

        service.dispatch([make_assessment(sender=SENDER)], commit=True)

        assert len(poster.posted) == 1

    def test_the_deny_list_wins_over_the_allow_list(self) -> None:
        settings = Settings(
            opt_in_only=True,
            allow_list=("them@example.com",),
            deny_list=("them@example.com",),
        )
        service, poster, _ = build(settings=settings)

        service.dispatch([make_assessment(sender=SENDER)], commit=True)

        assert poster.posted == []

    def test_the_deny_list_applies_even_when_not_opt_in_only(self) -> None:
        settings = Settings(opt_in_only=False, deny_list=("them@example.com",))
        service, poster, _ = build(settings=settings)

        service.dispatch([make_assessment(sender=SENDER)], commit=True)

        assert poster.posted == []

    def test_an_author_with_no_address_is_never_replied_to(self) -> None:
        """Without an address there is no way to honour a deny list or a cooldown."""
        anonymous = make_person(person_id="ghost", email="")
        service, poster, _ = build(settings=Settings(opt_in_only=False))

        service.dispatch([make_assessment(sender=anonymous)], commit=True)

        assert poster.posted == []


class TestCooldown:
    def test_someone_replied_to_recently_is_left_alone(self) -> None:
        """Article X.3: a burst inside one interaction draws one reply, not three."""
        log = FakeAuditLog(last_attempts={"them@example.com": NOW - timedelta(minutes=5)})
        service, poster, _ = build(audit=log)

        result = service.dispatch([make_assessment(sender=SENDER)], commit=True)

        assert poster.posted == []
        assert result.outcomes[0].withheld is WithheldReason.IN_COOLDOWN

    def test_someone_who_keeps_sending_greetings_keeps_getting_replies(self) -> None:
        """Past the window, a fresh greeting is a fresh offence and earns a fresh nudge."""
        log = FakeAuditLog(last_attempts={"them@example.com": NOW - timedelta(hours=2)})
        service, poster, _ = build(audit=log)

        service.dispatch([make_assessment(sender=SENDER)], commit=True)

        assert len(poster.posted) == 1

    def test_the_cooldown_is_matched_case_insensitively(self) -> None:
        log = FakeAuditLog(last_attempts={"THEM@example.com": NOW - timedelta(minutes=5)})
        service, poster, _ = build(audit=log)

        service.dispatch([make_assessment(sender=SENDER)], commit=True)

        assert poster.posted == []

    def test_a_zero_cooldown_permits_an_immediate_second_reply(self) -> None:
        """Only reachable by explicit configuration, so it must do what it says."""
        log = FakeAuditLog(last_attempts={"them@example.com": NOW})
        settings = Settings(opt_in_only=False, cooldown_minutes=0)
        service, poster, _ = build(settings=settings, audit=log)

        service.dispatch([make_assessment(sender=SENDER)], commit=True)

        assert len(poster.posted) == 1


class TestRunCap:
    def _many(self, count: int) -> list[Assessment]:
        return [
            make_assessment(sender=make_person(person_id=f"p{index}", email=f"p{index}@x.com"))
            for index in range(count)
        ]

    def test_the_cap_stops_further_sends(self) -> None:
        """Article X.5: post nothing beyond the cap."""
        settings = Settings(opt_in_only=False, max_replies_per_run=2)
        service, poster, _ = build(settings=settings)

        result = service.dispatch(self._many(5), commit=True)

        assert len(poster.posted) == 2
        assert result.hit_run_cap

    def test_exceeding_the_cap_is_reported_as_a_fault(self) -> None:
        """It must be loud: wanting to send twenty means something is wrong."""
        settings = Settings(opt_in_only=False, max_replies_per_run=1)
        service, _, _ = build(settings=settings)

        result = service.dispatch(self._many(3), commit=True)

        assert result.hit_run_cap
        held = [one for one in result.outcomes if one.withheld is WithheldReason.OVER_RUN_CAP]
        assert len(held) == 2

    def test_staying_under_the_cap_is_not_a_fault(self) -> None:
        settings = Settings(opt_in_only=False, max_replies_per_run=5)
        service, _, _ = build(settings=settings)

        result = service.dispatch(self._many(2), commit=True)

        assert not result.hit_run_cap

    def test_a_cap_of_zero_sends_nothing(self) -> None:
        settings = Settings(opt_in_only=False, max_replies_per_run=0)
        service, poster, _ = build(settings=settings)

        service.dispatch(self._many(3), commit=True)

        assert poster.posted == []


class TestOnlyGreetingsAreSent:
    def test_a_message_not_warranting_a_reply_is_never_dispatched(self) -> None:
        service, poster, _ = build()

        result = service.dispatch([make_assessment(sender=SENDER, warranted=False)], commit=True)

        assert poster.posted == []
        assert result.outcomes == ()


class TestAuditOrdering:
    def test_the_attempt_is_recorded_before_the_send(self) -> None:
        """Article X.7. A crash between the two costs a reply; the reverse duplicates one."""
        timeline: list[str] = []
        service, _, _ = build(
            audit=FakeAuditLog(timeline=timeline), webex=FakeWebexPoster(timeline=timeline)
        )

        service.dispatch([make_assessment(sender=SENDER)], commit=True)

        assert timeline == ["audit:attempted", "post", "audit:sent"]

    def test_a_confirmed_send_is_recorded(self) -> None:
        service, _, log = build()

        service.dispatch([make_assessment(sender=SENDER)], commit=True)

        events = [entry.event for entry in log.records]
        assert events == [ReplyEvent.ATTEMPTED, ReplyEvent.SENT]

    def test_the_record_carries_the_verdict_that_justified_it(self) -> None:
        """Without the reasoning, a misfire found later cannot be explained."""
        service, _, log = build()

        service.dispatch([make_assessment(sender=SENDER, reason="bare hello")], commit=True)

        first = log.records[0]
        assert first.verdict == "greeting_only"
        assert first.reason == "bare hello"
        assert first.confidence == pytest.approx(0.95)

    def test_a_reply_that_cannot_be_recorded_is_not_sent(self) -> None:
        """The record is what prevents a second reply, so without it nothing may go out."""
        log = FakeAuditLog(fail_on_record=True)
        service, poster, _ = build(audit=log)

        with pytest.raises(WebexApiError):
            service.dispatch([make_assessment(sender=SENDER)], commit=True)

        assert poster.posted == []


class TestFailedSends:
    def test_a_failed_send_is_recorded_and_not_retried(self) -> None:
        poster = FakeWebexPoster(fail_with=WebexApiError("Webex refused: [503]"))
        service, _, log = build(webex=poster)

        result = service.dispatch([make_assessment(sender=SENDER)], commit=True)

        assert result.failed
        assert [entry.event for entry in log.records] == [
            ReplyEvent.ATTEMPTED,
            ReplyEvent.FAILED,
        ]

    def test_a_failed_send_still_leaves_the_attempt_standing(self) -> None:
        """So the next run sees a cooldown and does not try again."""
        poster = FakeWebexPoster(fail_with=WebexApiError("Webex refused: [503]"))
        service, _, log = build(webex=poster)

        service.dispatch([make_assessment(sender=SENDER)], commit=True)

        assert any(entry.event is ReplyEvent.ATTEMPTED for entry in log.records)

    def test_one_failure_does_not_stop_the_rest_of_the_run(self) -> None:
        other = make_person(person_id="other", email="other@example.com")
        poster = FakeWebexPoster(fail_on_first=WebexApiError("Webex refused: [503]"))
        service, _, _ = build(webex=poster)

        result = service.dispatch(
            [make_assessment(sender=SENDER), make_assessment(sender=other)], commit=True
        )

        assert len(result.failed) == 1
        assert len(result.sent) == 1


class TestWhatIsPosted:
    def test_the_reply_is_a_threaded_reply_to_the_offending_message(self) -> None:
        """Article I.4: never a new top-level message."""
        service, poster, _ = build()

        service.dispatch([make_assessment(sender=SENDER)], commit=True)

        space_id, parent_id, body = poster.posted[0]
        assert space_id == "s1"
        assert parent_id == "m-latest"
        assert "nohello.net" in body

    def test_the_preview_matches_what_would_be_posted(self) -> None:
        service, poster, _ = build()
        assessment = make_assessment(sender=SENDER)

        preview = service.preview(assessment)
        service.dispatch([assessment], commit=True)

        assert poster.posted[0][2] == preview
