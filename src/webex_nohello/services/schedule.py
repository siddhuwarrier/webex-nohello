"""Choosing and describing the unattended schedule.

The protocol and the platform choice live here; each mechanism lives in its own module. Both
render their artefact as a pure function so it can be asserted verbatim (Article XIII.12) —
a scheduled job is the one thing nobody watches fail.
"""

from __future__ import annotations

import sys
from pathlib import Path
from shutil import which
from typing import Protocol

from webex_nohello.models.schedule.schedule_plan import SchedulePlan
from webex_nohello.models.schedule.schedule_state import ScheduleState
from webex_nohello.services.schedule_cron import CronScheduler
from webex_nohello.services.schedule_launchd import LaunchdScheduler, ScheduleError

EXECUTABLE_NAME = "webex-nohello"


class Scheduler(Protocol):
    @property
    def mechanism(self) -> str: ...

    @property
    def location(self) -> Path: ...

    def render(self, plan: SchedulePlan) -> str: ...

    def install(self, plan: SchedulePlan) -> None: ...

    def uninstall(self) -> bool: ...

    def state(self) -> ScheduleState: ...


def build_scheduler() -> Scheduler:
    return LaunchdScheduler() if sys.platform == "darwin" else CronScheduler()


def resolve_executable() -> Path:
    """The absolute path to this program's entry point.

    Article XIII.2: a scheduled job gets no useful `PATH`, so the schedule must name the
    executable outright. Checked next to the running interpreter first, which is what makes
    this work from a uv-managed virtualenv as well as from a `uv tool install`.
    """
    beside_interpreter = Path(sys.executable).parent / EXECUTABLE_NAME
    if beside_interpreter.exists():
        return beside_interpreter.resolve()

    found = which(EXECUTABLE_NAME)
    if found:
        return Path(found).resolve()

    raise ScheduleError(
        f"Could not find an installed '{EXECUTABLE_NAME}' to schedule.",
        remediation=(
            "A scheduled job cannot use 'uv run', because it has no working directory or "
            "PATH. Install the command first: uv tool install --editable ."
        ),
    )
