"""What is to be installed: a command, an interval, and where its output goes.

The command is held as an absolute path with its arguments spelled out, because Article
XIII.2 forbids depending on an inherited `PATH`, an activated virtualenv, or a shell
profile — none of which a launchd or cron job has. Article XIII.5 additionally requires
`--commit` be visible, so that reading the installed plist or crontab line tells the truth
about what it does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

SECONDS_PER_MINUTE = 60


@dataclass(frozen=True)
class SchedulePlan:
    executable: Path
    interval: timedelta
    log_file: Path
    arguments: tuple[str, ...] = ("run", "--commit")
    # The PATH the scheduled process gets. Not cosmetic: launchd leaves PATH unset, so a
    # user agent falls back to a minimal system default that excludes ~/.local/bin. Naming
    # our own executable absolutely is not enough, because the run then shells out to the
    # classifier CLI and that lookup fails.
    path_environment: str = ""

    @property
    def interval_minutes(self) -> int:
        return int(self.interval.total_seconds() // SECONDS_PER_MINUTE)

    @property
    def interval_seconds(self) -> int:
        return int(self.interval.total_seconds())

    @property
    def command(self) -> tuple[str, ...]:
        return (str(self.executable), *self.arguments)

    @property
    def is_committing(self) -> bool:
        """Whether this plan actually sends. A schedule that cannot is worth flagging."""
        return "--commit" in self.arguments
