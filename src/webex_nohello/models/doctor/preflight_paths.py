"""The files preflight inspects, gathered so they travel as one value.

Passed in rather than read from `paths` directly, so the checks can be pointed at a
temporary directory in tests without touching the operator's real state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from webex_nohello import paths


@dataclass(frozen=True)
class PreflightPaths:
    config: Path
    reply_template: Path
    state_directory: Path
    scan_state: Path
    paused: Path

    @classmethod
    def real(cls) -> PreflightPaths:
        return cls(
            config=paths.config_file(),
            reply_template=paths.reply_template_file(),
            state_directory=paths.state_directory(),
            scan_state=paths.scan_state_file(),
            paused=paths.paused_file(),
        )
