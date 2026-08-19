"""The scheduler for everything that is not macOS: a marked block in the user's crontab.

The markers matter. Rewriting someone's crontab is destructive, so everything outside the
block is copied through verbatim and only our own lines are replaced.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from webex_nohello.models.schedule.schedule_plan import SchedulePlan
from webex_nohello.models.schedule.schedule_state import ScheduleState
from webex_nohello.services.schedule_launchd import ScheduleError, run_quietly

CRON_BEGIN = "# >>> webex-nohello >>>"
CRON_END = "# <<< webex-nohello <<<"
MINUTES_PER_HOUR = 60


class CronScheduler:
    """crontab, for Linux.

    The managed block is delimited by markers so that an operator's own entries are left
    untouched. Anything outside the markers is copied through verbatim.
    """

    @property
    def mechanism(self) -> str:
        return "crontab"

    @property
    def location(self) -> Path:
        return Path("crontab")

    def render(self, plan: SchedulePlan) -> str:
        command = " ".join(plan.command)
        return (
            f"{CRON_BEGIN}\n"
            f"{_cron_expression(plan.interval_minutes)} "
            f"{command} >> {plan.log_file} 2>&1\n"
            f"{CRON_END}\n"
        )

    def install(self, plan: SchedulePlan) -> None:
        _write_crontab(_without_managed_block(_read_crontab()) + self.render(plan))

    def uninstall(self) -> bool:
        current = _read_crontab()
        if CRON_BEGIN not in current:
            return False
        _write_crontab(_without_managed_block(current))
        return True

    def state(self) -> ScheduleState:
        current = _read_crontab()
        if CRON_BEGIN not in current:
            return ScheduleState(
                is_installed=False, location=self.location, description="not installed"
            )
        return ScheduleState(
            is_installed=True,
            location=self.location,
            description="present in your crontab",
            is_loaded=True,
        )


def _cron_expression(minutes: int) -> str:
    """A cron schedule for an interval in minutes.

    cron has no notion of "every N minutes" beyond an hour, so anything above 60 must be
    expressed in hours and must divide evenly. Refusing is better than silently rounding to
    something the operator did not ask for.
    """
    if minutes < 1:
        raise ScheduleError(
            "An interval of less than a minute cannot be scheduled.",
            remediation="Choose at least one minute.",
        )
    if minutes < MINUTES_PER_HOUR:
        if MINUTES_PER_HOUR % minutes != 0:
            raise ScheduleError(
                f"cron cannot run every {minutes} minutes evenly: {minutes} does not divide 60, "
                "so the last run of each hour would be a different length.",
                remediation="Choose an interval that divides 60: 5, 10, 15, 20 or 30.",
            )
        return f"*/{minutes} * * * *"
    if minutes == MINUTES_PER_HOUR:
        return "0 * * * *"
    if minutes % MINUTES_PER_HOUR != 0:
        raise ScheduleError(
            f"An interval of {minutes} minutes is not a whole number of hours.",
            remediation="Above an hour, choose a multiple of 60.",
        )
    return f"0 */{minutes // MINUTES_PER_HOUR} * * *"


def _read_crontab() -> str:
    result = run_quietly(["crontab", "-l"])
    # A user with no crontab yet exits non-zero; that is not an error.
    return result.stdout if result.returncode == 0 else ""


def _write_crontab(content: str) -> None:
    try:
        result = subprocess.run(  # fixed argv, content on stdin
            ["crontab", "-"],  # noqa: S607  # resolved from PATH deliberately
            input=content,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ScheduleError(
            "'crontab' is not available on this system.",
            remediation="Install cron, or schedule the command with your own init system.",
        ) from exc

    if result.returncode != 0:
        raise ScheduleError(
            f"crontab refused the new schedule: {result.stderr.strip() or 'no output'}",
            remediation="Check 'crontab -l' and try again.",
        )


def _without_managed_block(crontab: str) -> str:
    """Strip our block, leaving everything the operator wrote alone."""
    lines = crontab.splitlines(keepends=True)
    kept: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == CRON_BEGIN:
            inside = True
            continue
        if line.strip() == CRON_END:
            inside = False
            continue
        if not inside:
            kept.append(line)

    result = "".join(kept)
    if result and not result.endswith("\n"):
        result += "\n"
    return result
