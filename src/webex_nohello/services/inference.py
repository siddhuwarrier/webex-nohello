"""What every classifier CLI has in common.

Separate from `agent_cli` so the drivers can import the contract without importing the
chooser, which would be a cycle. Nothing here invokes anything.
"""

from __future__ import annotations

from typing import Protocol

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError

DEFAULT_TIMEOUT_SECONDS = 90.0
MAX_ERROR_EXCERPT = 400


class InferenceError(WebexNoHelloError):
    pass


class InferenceDriver(Protocol):
    @property
    def name(self) -> str: ...

    def command_for(self, prompt: str, system_prompt: str) -> list[str]:
        """The exact command line, for `--explain`. Article IX.10 requires this be printable."""
        ...

    def complete(self, prompt: str, system_prompt: str) -> str: ...
