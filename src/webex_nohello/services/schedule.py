"""Choosing and describing the unattended schedule.

The protocol and the platform choice live here; each mechanism lives in its own module. Both
render their artefact as a pure function so it can be asserted verbatim (Article XIII.13) —
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
CLASSIFIER_NAME = "claude"

# What a login shell would usually have, and what launchd notably does not.
BASELINE_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")


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
        return beside_interpreter.absolute()

    found = which(EXECUTABLE_NAME)
    if found:
        # `absolute`, not `resolve`: see the note in resolve_path_environment about symlinks
        # into versioned directories.
        return Path(found).absolute()

    raise ScheduleError(
        f"Could not find an installed '{EXECUTABLE_NAME}' to schedule.",
        remediation=(
            "A scheduled job cannot use 'uv run', because it has no working directory or "
            "PATH. Install the command first: uv tool install --editable ."
        ),
    )


def resolve_path_environment() -> str:
    """The PATH a scheduled run needs, built from where things actually are.

    launchd leaves PATH unset for a user agent, and cron sets a minimal one. Neither
    includes `~/.local/bin`, which is where `claude` and a `uv tool install` both land — so
    a scheduled run finds neither, having worked perfectly from a shell. This resolves both
    at install time and pins the result into the artefact.

    Order matters: the directories we actually found come first, so a scheduled run uses the
    same binaries the operator tested with rather than an older copy earlier on the path.
    """
    directories: list[str] = []

    for name in (EXECUTABLE_NAME, CLASSIFIER_NAME):
        found = which(name)
        if found:
            # Deliberately NOT resolved. `which` already gives an absolute path, and both
            # `claude` and a uv-installed shim in ~/.local/bin are symlinks into a
            # version-specific directory. Following them pins today's version, which breaks
            # silently the next time the tool updates itself.
            parent = str(Path(found).parent)
            if parent not in directories:
                directories.append(parent)

    directories.extend(entry for entry in BASELINE_PATH if entry not in directories)
    return ":".join(directories)
