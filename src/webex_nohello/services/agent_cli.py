"""Running an agent CLI to get one classification.

Article III.2: inference goes through the operator's existing `claude` installation, so no
separate API key is needed. Article III.3: nothing outside this module knows which CLI is
in use — `codex` is not implemented yet and will arrive as a second class behind
`InferenceDriver`, changing nothing else.

The invocation is shaped by measurement rather than by documentation:

  * stdin MUST be closed. `claude -p` waits three seconds for piped input before giving
    up, which was most of the wall clock for a short prompt.
  * `--system-prompt` replaces Claude Code's own, and `--strict-mcp-config` drops the
    operator's MCP servers. Together they cut the context from roughly 25,000 tokens to
    3,600: the agent harness is otherwise sent in full on every call.
  * The system prompt is a stable constant, so the cached prefix is reused between calls.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Protocol

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError

DEFAULT_TIMEOUT_SECONDS = 90.0
DEFAULT_MODEL = "haiku"
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


class ClaudeDriver:
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        executable: str = "claude",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._model = model
        self._executable = executable
        self._timeout = timeout

    @property
    def name(self) -> str:
        return f"claude ({self._model})"

    def command_for(self, prompt: str, system_prompt: str) -> list[str]:
        return [
            self._executable,
            "--print",
            prompt,
            "--model",
            self._model,
            "--output-format",
            "json",
            "--system-prompt",
            system_prompt,
            # Article IX.4: no tools. `--strict-mcp-config` also drops the operator's Webex
            # MCP server, so the classifier cannot reach Webex even in principle.
            "--strict-mcp-config",
            "--allowedTools",
            "",
        ]

    def complete(self, prompt: str, system_prompt: str) -> str:
        command = self.command_for(prompt, system_prompt)
        try:
            completed = subprocess.run(  # noqa: S603  # built here from constants, not input
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                # Closing stdin is not tidiness: `claude -p` otherwise waits three seconds
                # for input that will never arrive.
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except FileNotFoundError as exc:
            raise InferenceError(
                f"Could not run '{self._executable}': it is not on PATH.",
                remediation="Install Claude Code: https://docs.claude.com/en/docs/claude-code",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise InferenceError(
                f"'{self._executable}' did not answer within {self._timeout:.0f} seconds.",
                remediation="Check the CLI works interactively, or raise --classifier-timeout.",
            ) from exc

        if completed.returncode != 0:
            raise InferenceError(
                f"'{self._executable}' exited {completed.returncode}: "
                f"{completed.stderr.strip()[:MAX_ERROR_EXCERPT] or 'no output'}",
                remediation="Check the CLI is authenticated by running it interactively.",
            )
        return _text_from_envelope(completed.stdout)


def _text_from_envelope(stdout: str) -> str:
    """Pull the assistant's text out of `--output-format json`."""
    try:
        envelope = json.loads(stdout)
    except ValueError as exc:
        raise InferenceError(
            f"Could not parse the JSON envelope from claude: {stdout.strip()[:MAX_ERROR_EXCERPT]}",
            remediation="Check whether the installed claude changed --output-format json.",
        ) from exc

    if not isinstance(envelope, dict):
        raise InferenceError(f"claude returned {type(envelope).__name__}, expected an object")

    if envelope.get("is_error"):
        raise InferenceError(
            f"claude reported an error: {str(envelope.get('result'))[:MAX_ERROR_EXCERPT]}",
            remediation="Run the same prompt interactively to see the failure.",
        )

    result = envelope.get("result")
    if not isinstance(result, str) or not result.strip():
        raise InferenceError("claude returned an empty result")
    return result


def build_driver(
    *, model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> InferenceDriver:
    if not shutil.which("claude"):
        raise InferenceError(
            "'claude' is not on PATH, so messages cannot be classified.",
            remediation=(
                "Install Claude Code and sign in: https://docs.claude.com/en/docs/claude-code"
            ),
        )
    return ClaudeDriver(model=model, timeout=timeout)
