"""Schedulers: the exact artefact installed, and the cron arithmetic.

Article XIII.12 asks for these verbatim. A scheduled job is the one thing nobody watches
fail, so a stray character in a plist or a wrong cron field would go unnoticed for days.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from webex_nohello.models.schedule.schedule_plan import SchedulePlan
from webex_nohello.services.schedule_cron import (
    CRON_BEGIN,
    CRON_END,
    CronScheduler,
    _cron_expression,
    _without_managed_block,
)
from webex_nohello.services.schedule_launchd import LaunchdScheduler, ScheduleError

PLAN = SchedulePlan(
    executable=Path("/opt/homebrew/bin/webex-nohello"),
    interval=timedelta(minutes=10),
    log_file=Path("/home/example/state/run.log"),
)

EXPECTED_PLIST = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>local.webex-nohello</string>
    <key>ProgramArguments</key>
    <array>
      <string>/opt/homebrew/bin/webex-nohello</string>
      <string>run</string>
      <string>--commit</string>
    </array>
    <key>StartInterval</key>
    <integer>600</integer>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>/home/example/state/run.log</string>
    <key>StandardErrorPath</key>
    <string>/home/example/state/run.log</string>
    <key>ProcessType</key>
    <string>Background</string>
    <key>LowPriorityIO</key>
    <true/>
  </dict>
</plist>
"""


class TestLaunchdArtefact:
    def test_the_plist_is_exactly_this(self, tmp_path: Path) -> None:
        assert LaunchdScheduler(tmp_path).render(PLAN) == EXPECTED_PLIST

    def test_the_executable_is_an_absolute_path(self, tmp_path: Path) -> None:
        """Article XIII.2: launchd inherits no useful PATH."""
        rendered = LaunchdScheduler(tmp_path).render(PLAN)

        assert "<string>/opt/homebrew/bin/webex-nohello</string>" in rendered

    def test_commit_is_visible_in_the_artefact(self, tmp_path: Path) -> None:
        """Article XIII.4: reading the plist must tell the truth about what it does."""
        assert "<string>--commit</string>" in LaunchdScheduler(tmp_path).render(PLAN)

    def test_it_does_not_run_at_load(self, tmp_path: Path) -> None:
        """Installing should not immediately send; the operator has not been asked yet."""
        rendered = LaunchdScheduler(tmp_path).render(PLAN)

        assert "<key>RunAtLoad</key>\n    <false/>" in rendered

    def test_output_is_captured_to_a_file(self, tmp_path: Path) -> None:
        """Nobody is watching the terminal, so a scheduled failure must land somewhere."""
        rendered = LaunchdScheduler(tmp_path).render(PLAN)

        assert rendered.count("<string>/home/example/state/run.log</string>") == 2

    def test_the_interval_is_in_seconds(self, tmp_path: Path) -> None:
        """StartInterval is seconds; minutes here would run 60 times too often."""
        assert "<integer>600</integer>" in LaunchdScheduler(tmp_path).render(PLAN)

    def test_the_plist_location_is_the_user_agents_directory(self, tmp_path: Path) -> None:
        assert LaunchdScheduler(tmp_path).location == tmp_path / "local.webex-nohello.plist"


class TestCronArtefact:
    def test_the_block_is_exactly_this(self) -> None:
        expected = (
            f"{CRON_BEGIN}\n"
            "*/10 * * * * /opt/homebrew/bin/webex-nohello run --commit "
            ">> /home/example/state/run.log 2>&1\n"
            f"{CRON_END}\n"
        )

        assert CronScheduler().render(PLAN) == expected

    def test_output_is_redirected_including_stderr(self) -> None:
        assert ">> /home/example/state/run.log 2>&1" in CronScheduler().render(PLAN)

    def test_commit_is_visible(self) -> None:
        assert "run --commit" in CronScheduler().render(PLAN)


class TestCronExpression:
    @pytest.mark.parametrize(
        ("minutes", "expected"),
        [
            (1, "*/1 * * * *"),
            (5, "*/5 * * * *"),
            (10, "*/10 * * * *"),
            (15, "*/15 * * * *"),
            (30, "*/30 * * * *"),
            (60, "0 * * * *"),
            (120, "0 */2 * * *"),
            (360, "0 */6 * * *"),
        ],
    )
    def test_supported_intervals(self, minutes: int, expected: str) -> None:
        assert _cron_expression(minutes) == expected

    @pytest.mark.parametrize("minutes", [7, 11, 25, 45, 50])
    def test_an_interval_that_does_not_divide_an_hour_is_refused(self, minutes: int) -> None:
        """Silently rounding would give the operator a schedule they did not ask for."""
        with pytest.raises(ScheduleError) as caught:
            _cron_expression(minutes)

        assert caught.value.remediation is not None
        assert "divides 60" in caught.value.remediation

    @pytest.mark.parametrize("minutes", [90, 150])
    def test_an_interval_above_an_hour_must_be_whole_hours(self, minutes: int) -> None:
        with pytest.raises(ScheduleError):
            _cron_expression(minutes)

    @pytest.mark.parametrize("minutes", [0, -5])
    def test_a_non_positive_interval_is_refused(self, minutes: int) -> None:
        with pytest.raises(ScheduleError):
            _cron_expression(minutes)


class TestCrontabEditing:
    def test_the_operators_own_entries_are_preserved(self) -> None:
        """Rewriting someone's crontab is destructive, so everything else is copied through."""
        existing = (
            "# my own jobs\n"
            "0 9 * * 1 /usr/local/bin/weekly-report\n"
            f"{CRON_BEGIN}\n"
            "*/15 * * * * /old/path run --commit\n"
            f"{CRON_END}\n"
            "30 2 * * * /usr/local/bin/backup\n"
        )

        stripped = _without_managed_block(existing)

        assert "weekly-report" in stripped
        assert "backup" in stripped
        assert "/old/path" not in stripped
        assert CRON_BEGIN not in stripped

    def test_stripping_an_absent_block_changes_nothing(self) -> None:
        existing = "0 9 * * 1 /usr/local/bin/weekly-report\n"

        assert _without_managed_block(existing) == existing

    def test_an_empty_crontab_stays_empty(self) -> None:
        assert _without_managed_block("") == ""

    def test_a_missing_trailing_newline_is_added(self) -> None:
        """crontab rejects a file whose last line has no newline."""
        assert _without_managed_block("0 9 * * 1 job").endswith("\n")

    def test_reinstalling_replaces_rather_than_duplicates(self) -> None:
        """Two blocks would mean two runs firing at once, which the lock would then reject."""
        existing = f"{CRON_BEGIN}\n*/15 * * * * /old run --commit\n{CRON_END}\n"

        rewritten = _without_managed_block(existing) + CronScheduler().render(PLAN)

        assert rewritten.count(CRON_BEGIN) == 1


class TestPlan:
    def test_the_interval_is_exposed_in_both_units(self) -> None:
        plan = SchedulePlan(
            executable=Path("/x"), interval=timedelta(minutes=10), log_file=Path("/y")
        )

        assert plan.interval_minutes == 10
        assert plan.interval_seconds == 600

    def test_a_committing_plan_is_recognisable(self) -> None:
        assert PLAN.is_committing

    def test_a_dry_run_plan_is_not(self) -> None:
        plan = SchedulePlan(
            executable=Path("/x"),
            interval=timedelta(minutes=10),
            log_file=Path("/y"),
            arguments=("run",),
        )

        assert not plan.is_committing
