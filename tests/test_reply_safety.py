"""The rails that live outside DispatchService: the lock, the kill switch, the log, the text.

All of Article X's remaining guarantees, plus Article XI's template rules. These use real
files in a temporary directory, because what is being tested is durability and exclusion —
both properties of the filesystem rather than of the code alone.
"""

from __future__ import annotations

import multiprocessing
from datetime import UTC, datetime, timedelta
from multiprocessing.synchronize import Event as EventType
from pathlib import Path

import pytest

from conftest import NOW, make_person
from webex_nohello.models.audit.reply_record import ReplyEvent, ReplyRecord
from webex_nohello.models.config.settings import DEFAULT_COOLDOWN_MINUTES
from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError
from webex_nohello.services.audit import FileAuditLog, is_in_cooldown
from webex_nohello.services.lock import AlreadyRunningError, is_paused, run_lock
from webex_nohello.services.reply_template import DEFAULT_TEMPLATE, load_reply, locate, render

SUBPROCESS_TIMEOUT = 30


def a_record(
    *,
    event: ReplyEvent = ReplyEvent.ATTEMPTED,
    recipient: str = "them@example.com",
    at: datetime | None = None,
) -> ReplyRecord:
    return ReplyRecord(
        event=event,
        at=at if at is not None else NOW,
        space_id="s1",
        recipient_email=recipient,
        replied_to_message_id="m1",
        verdict="greeting_only",
        confidence=0.95,
        reason="bare greeting",
        excerpt="hi",
    )


class TestAuditLogPersistence:
    def test_a_record_survives_a_reread(self, tmp_path: Path) -> None:
        log = FileAuditLog(tmp_path / "replies.jsonl")

        log.record(a_record())

        assert FileAuditLog(tmp_path / "replies.jsonl").last_attempt_to("them@example.com") == NOW

    def test_the_log_is_append_only(self, tmp_path: Path) -> None:
        """Nothing is ever rewritten: the history is the evidence for every cooldown."""
        path = tmp_path / "replies.jsonl"
        log = FileAuditLog(path)

        log.record(a_record(at=NOW - timedelta(days=40)))
        log.record(a_record(at=NOW))

        assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2

    def test_the_latest_attempt_wins(self, tmp_path: Path) -> None:
        log = FileAuditLog(tmp_path / "replies.jsonl")
        log.record(a_record(at=NOW - timedelta(days=40)))
        log.record(a_record(at=NOW))

        assert log.last_attempt_to("them@example.com") == NOW

    def test_an_absent_log_means_nobody_has_been_replied_to(self, tmp_path: Path) -> None:
        assert FileAuditLog(tmp_path / "nothing.jsonl").last_attempt_to("them@example.com") is None

    def test_only_attempts_count_towards_a_cooldown(self, tmp_path: Path) -> None:
        """A SENT line is a confirmation, not a second reply."""
        log = FileAuditLog(tmp_path / "replies.jsonl")

        log.record(a_record(event=ReplyEvent.SENT, at=NOW))

        assert log.last_attempt_to("them@example.com") is None

    def test_another_persons_history_is_not_consulted(self, tmp_path: Path) -> None:
        log = FileAuditLog(tmp_path / "replies.jsonl")
        log.record(a_record(recipient="someone@else.com"))

        assert log.last_attempt_to("them@example.com") is None

    def test_a_corrupt_line_is_reported_rather_than_ignored(self, tmp_path: Path) -> None:
        """Silently skipping it would silently drop a cooldown, and so send a second reply."""
        path = tmp_path / "replies.jsonl"
        log = FileAuditLog(path)
        log.record(a_record())
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{ this is not valid json\n")

        with pytest.raises(WebexNoHelloError) as caught:
            log.last_attempt_to("them@example.com")

        assert "unreadable" in caught.value.message

    def test_blank_lines_are_tolerated(self, tmp_path: Path) -> None:
        path = tmp_path / "replies.jsonl"
        log = FileAuditLog(path)
        log.record(a_record())
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n\n")

        assert log.last_attempt_to("them@example.com") == NOW


class TestCooldownArithmetic:
    WINDOW = timedelta(minutes=DEFAULT_COOLDOWN_MINUTES)

    def test_no_prior_attempt_is_not_a_cooldown(self) -> None:
        assert not is_in_cooldown(None, NOW, self.WINDOW)

    def test_inside_the_window_is_a_cooldown(self) -> None:
        assert is_in_cooldown(NOW - timedelta(minutes=29), NOW, self.WINDOW)

    def test_exactly_at_the_window_is_not(self) -> None:
        assert not is_in_cooldown(NOW - self.WINDOW, NOW, self.WINDOW)

    def test_beyond_the_window_is_not(self) -> None:
        assert not is_in_cooldown(NOW - timedelta(minutes=31), NOW, self.WINDOW)

    def test_a_zero_window_never_holds_anything_back(self) -> None:
        assert not is_in_cooldown(NOW, NOW, timedelta())


def _hold_lock(path: str, ready: EventType, release: EventType) -> None:
    """Runs in a separate process: an flock is per-process, so a thread would not prove much."""
    with run_lock(Path(path)):
        ready.set()
        release.wait(SUBPROCESS_TIMEOUT)


class TestRunLock:
    def test_a_lock_can_be_taken_and_released(self, tmp_path: Path) -> None:
        path = tmp_path / "run.lock"

        with run_lock(path):
            pass

        with run_lock(path):
            pass

    def test_a_second_run_is_refused_while_the_first_holds_it(self, tmp_path: Path) -> None:
        """Article X.9: overlapping runs are the obvious route to a double reply."""
        path = tmp_path / "run.lock"
        context = multiprocessing.get_context("spawn")
        ready, release = context.Event(), context.Event()
        holder = context.Process(target=_hold_lock, args=(str(path), ready, release))
        holder.start()
        try:
            assert ready.wait(SUBPROCESS_TIMEOUT), "the holding process never acquired the lock"

            with pytest.raises(AlreadyRunningError), run_lock(path):
                pass
        finally:
            release.set()
            holder.join(SUBPROCESS_TIMEOUT)

    def test_the_lock_is_released_when_the_holder_dies(self, tmp_path: Path) -> None:
        """flock rather than a pid file, so a killed run cannot leave a stale lock."""
        path = tmp_path / "run.lock"
        context = multiprocessing.get_context("spawn")
        ready, release = context.Event(), context.Event()
        holder = context.Process(target=_hold_lock, args=(str(path), ready, release))
        holder.start()
        assert ready.wait(SUBPROCESS_TIMEOUT)
        holder.kill()
        holder.join(SUBPROCESS_TIMEOUT)

        with run_lock(path):
            pass


class TestKillSwitch:
    def test_an_absent_file_means_not_paused(self, tmp_path: Path) -> None:
        assert not is_paused(tmp_path / "PAUSED")

    def test_the_files_existence_alone_pauses(self, tmp_path: Path) -> None:
        """Contents are deliberately irrelevant: `touch` must be enough."""
        marker = tmp_path / "PAUSED"
        marker.write_text("", encoding="utf-8")

        assert is_paused(marker)


class TestWhereTheReplyComesFrom:
    """Article XI.3. The operator must be able to see which file is in force."""

    def test_the_default_location_is_beside_the_config(self, tmp_path: Path) -> None:
        default = tmp_path / "reply.md"

        assert locate(None, default_path=default) == default

    def test_a_relative_path_is_relative_to_the_config_file(self, tmp_path: Path) -> None:
        """Otherwise a config could not be copied between machines."""
        resolved = locate(Path("prose/mine.md"), default_path=tmp_path / "reply.md")

        assert resolved == tmp_path / "prose" / "mine.md"

    def test_an_absolute_path_is_taken_as_given(self, tmp_path: Path) -> None:
        elsewhere = tmp_path / "elsewhere" / "mine.md"

        assert locate(elsewhere, default_path=tmp_path / "reply.md") == elsewhere

    def test_a_tilde_is_expanded(self, tmp_path: Path) -> None:
        resolved = locate(Path("~/mine.md"), default_path=tmp_path / "reply.md")

        assert resolved == Path.home() / "mine.md"


class TestReplyTemplate:
    def test_the_default_is_used_when_no_file_exists(self, tmp_path: Path) -> None:
        source = load_reply(None, default_path=tmp_path / "reply.md")

        assert source.text == DEFAULT_TEMPLATE
        assert not source.is_customised
        # Named even though it is absent: it is the file to create to change the wording.
        assert source.path == tmp_path / "reply.md"

    def test_the_default_says_what_it_needs_to(self) -> None:
        assert "nohello.net" in DEFAULT_TEMPLATE
        assert "Automated reply" in DEFAULT_TEMPLATE

    def test_a_file_at_the_default_location_replaces_the_default(self, tmp_path: Path) -> None:
        path = tmp_path / "reply.md"
        path.write_text("my own wording", encoding="utf-8")

        source = load_reply(None, default_path=path)

        assert source.text == "my own wording"
        assert source.is_customised
        assert source.path == path

    def test_a_configured_file_is_read_and_named(self, tmp_path: Path) -> None:
        mine = tmp_path / "prose" / "mine.md"
        mine.parent.mkdir()
        mine.write_text("from my notes", encoding="utf-8")

        source = load_reply(Path("prose/mine.md"), default_path=tmp_path / "reply.md")

        assert source.text == "from my notes"
        assert source.path == mine

    def test_a_configured_file_that_is_missing_is_refused(self, tmp_path: Path) -> None:
        """Sending the built-in default instead would put words in the operator's mouth."""
        with pytest.raises(WebexNoHelloError) as caught:
            load_reply(Path("gone.md"), default_path=tmp_path / "reply.md")

        assert "gone.md" in caught.value.message

    def test_an_empty_file_is_refused(self, tmp_path: Path) -> None:
        """Rendering nothing would post an empty message, which is worse than not posting."""
        path = tmp_path / "reply.md"
        path.write_text("   \n", encoding="utf-8")

        with pytest.raises(WebexNoHelloError):
            load_reply(None, default_path=path)

    def test_the_default_renders_unchanged(self) -> None:
        sender = make_person(display_name="Ada Lovelace", email="ada@example.com")

        assert render(DEFAULT_TEMPLATE, sender) == DEFAULT_TEMPLATE

    def test_supported_placeholders_are_substituted(self) -> None:
        sender = make_person(display_name="Ada Lovelace", email="ada@example.com")

        rendered = render("Hi {sender_first_name} ({sender_email})", sender)

        assert rendered == "Hi Ada (ada@example.com)"

    def test_an_unknown_placeholder_is_refused(self) -> None:
        """Article XI.4: rendering it literally would be worse than not replying at all."""
        sender = make_person(display_name="Ada", email="ada@example.com")

        with pytest.raises(WebexNoHelloError) as caught:
            render("Hi {their_name}", sender)

        assert "their_name" in caught.value.message
        assert caught.value.remediation is not None
        assert "sender_first_name" in caught.value.remediation

    def test_a_sender_with_no_display_name_renders_empty_rather_than_failing(self) -> None:
        sender = make_person(display_name="", email="ada@example.com")

        assert render("Hi {sender_first_name}", sender) == "Hi "


def test_records_are_timezone_aware() -> None:
    """A naive timestamp would make the cooldown comparison raise at the worst moment."""
    record = a_record(at=datetime.now(UTC))

    assert record.at.tzinfo is not None
