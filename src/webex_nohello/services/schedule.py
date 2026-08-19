"""Installing and removing the unattended schedule.

Two implementations behind one protocol: launchd on macOS, crontab elsewhere. Rendering is
kept as a pure function on each so the exact installed artefact can be asserted verbatim
(Article XIII.12) — a scheduled job is the one thing nobody watches fail, so its text is
worth pinning character for character.

Three properties matter more than the mechanism:

  * The command is an absolute path (Article XIII.2). Neither launchd nor cron inherits a
    usable `PATH`, and this is the most common way a scheduled job silently does nothing.
  * `--commit` appears in the artefact (Article XIII.4), so reading it tells the truth.
  * Uninstall is complete and idempotent (Article XIII.6).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from shutil import which
from typing import Protocol

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError
from webex_nohello.models.schedule.schedule_plan import SchedulePlan
from webex_nohello.models.schedule.schedule_state import ScheduleState

LABEL = "local.webex-nohello"
CRON_BEGIN = "# >>> webex-nohello >>>"
CRON_END = "# <<< webex-nohello <<<"
MINUTES_PER_HOUR = 60
EXECUTABLE_NAME = "webex-nohello"


class ScheduleError(WebexNoHelloError):
    pass


class Scheduler(Protocol):
    @property
    def mechanism(self) -> str: ...

    @property
    def location(self) -> Path: ...

    def render(self, plan: SchedulePlan) -> str: ...

    def install(self, plan: SchedulePlan) -> None: ...

    def uninstall(self) -> bool: ...

    def state(self) -> ScheduleState: ...


class LaunchdScheduler:
    def __init__(self, agents_directory: Path | None = None) -> None:
        self._directory = (
            agents_directory
            if agents_directory is not None
            else Path.home() / "Library" / "LaunchAgents"
        )

    @property
    def mechanism(self) -> str:
        return "launchd"

    @property
    def location(self) -> Path:
        return self._directory / f"{LABEL}.plist"

    def render(self, plan: SchedulePlan) -> str:
        arguments = "\n".join(f"      <string>{part}</string>" for part in plan.command)
        return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{arguments}
    </array>
    <key>StartInterval</key>
    <integer>{plan.interval_seconds}</integer>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{plan.log_file}</string>
    <key>StandardErrorPath</key>
    <string>{plan.log_file}</string>
    <key>ProcessType</key>
    <string>Background</string>
    <key>LowPriorityIO</key>
    <true/>
  </dict>
</plist>
"""

    def install(self, plan: SchedulePlan) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        # Unloaded first: launchd ignores a rewritten plist until it is reloaded, so
        # installing over an existing schedule would otherwise keep the old interval.
        self._bootout()
        self.location.write_text(self.render(plan), encoding="utf-8")
        self._bootstrap()

    def uninstall(self) -> bool:
        existed = self.location.exists()
        self._bootout()
        self.location.unlink(missing_ok=True)
        return existed

    def state(self) -> ScheduleState:
        if not self.location.exists():
            return ScheduleState(
                is_installed=False, location=self.location, description="not installed"
            )

        loaded = self._is_loaded()
        return ScheduleState(
            is_installed=True,
            location=self.location,
            description="loaded and scheduled" if loaded else "on disk but not loaded by launchd",
            is_loaded=loaded,
        )

    def _domain(self) -> str:
        return f"gui/{os.getuid()}"

    def _bootout(self) -> None:
        # Failure is expected and ignored: nothing is loaded the first time round.
        _run_quietly(["launchctl", "bootout", f"{self._domain()}/{LABEL}"])

    def _bootstrap(self) -> None:
        result = _run_quietly(["launchctl", "bootstrap", self._domain(), str(self.location)])
        if result.returncode != 0:
            raise ScheduleError(
                f"launchctl refused to load {self.location}: "
                f"{result.stderr.strip() or result.stdout.strip() or 'no output'}",
                remediation=(
                    "The plist has been written. Load it by hand with: launchctl bootstrap "
                    f"{self._domain()} {self.location}"
                ),
            )

    def _is_loaded(self) -> bool:
        return _run_quietly(["launchctl", "print", f"{self._domain()}/{LABEL}"]).returncode == 0


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
    result = _run_quietly(["crontab", "-l"])
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


def _run_quietly(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603  # fixed argv built from constants
            command, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(command, returncode=127, stdout="", stderr="not found")


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
