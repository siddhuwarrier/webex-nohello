"""Whether a schedule is installed, and what it says."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScheduleState:
    is_installed: bool
    # Where the schedule lives, so the operator can read it themselves.
    location: Path
    description: str
    # True when the installed schedule was loaded by the system, not merely written to disk.
    # A plist on disk that launchd has not loaded runs never, which is worth distinguishing.
    is_loaded: bool = False
