"""The macOS scheduler: a launchd user agent.

`StartInterval` rather than `StartCalendarInterval`, because the operator asks for "every N
minutes" and launchd measures that in seconds from the last run — which also means a laptop
that sleeps simply resumes rather than firing every missed interval at once.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError
from webex_nohello.models.schedule.schedule_plan import SchedulePlan
from webex_nohello.models.schedule.schedule_state import ScheduleState

LABEL = "local.webex-nohello"


class ScheduleError(WebexNoHelloError):
    pass


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
        run_quietly(["launchctl", "bootout", f"{self._domain()}/{LABEL}"])

    def _bootstrap(self) -> None:
        result = run_quietly(["launchctl", "bootstrap", self._domain(), str(self.location)])
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
        return run_quietly(["launchctl", "print", f"{self._domain()}/{LABEL}"]).returncode == 0


def run_quietly(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a scheduling tool, returning its result rather than raising.

    Shared with the cron scheduler. A missing binary comes back as exit 127 instead of an
    exception, because several call sites expect failure as a normal outcome — `launchctl
    bootout` fails the first time round, when nothing is loaded yet.
    """
    try:
        return subprocess.run(  # noqa: S603  # fixed argv built from constants
            command, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(command, returncode=127, stdout="", stderr="not found")
