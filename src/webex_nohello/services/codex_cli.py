"""Classifying with `codex`.

Two things about this invocation are load-bearing and neither is obvious.

**An isolated CODEX_HOME.** Article IX.4 requires the classifier have no tools, and in
particular no route to Webex. `claude` offers `--strict-mcp-config`; `codex` has no
equivalent. Its MCP servers come from *plugins* in `~/.codex/config.toml`, and `-c
plugins={}`, `-c features.plugins=false` and `-c mcp_servers={}` were all measured to leave
them loading anyway — on the machine this was written, that included the operator's own
Webex MCP server. So `codex` is pointed at a home directory of our own containing no config
at all, which loads no plugins and therefore exposes no tools.

The credentials are **symlinked, never copied**. A second copy of an auth token on disk is a
worse problem than the one being solved.

**The home is reused.** A fresh one costs about 17 seconds while caches are built; a warm one
answers in 4 to 6, comparable to claude. Creating a temporary home per call would make every
classification the slow case.

The answer is read from `--output-last-message` rather than parsed out of stdout, which
carries session preamble and log lines that would have to be stripped heuristically.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from webex_nohello import paths
from webex_nohello.services.inference import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_ERROR_EXCERPT,
    InferenceError,
)

AUTH_FILENAME = "auth.json"
ANSWER_FILENAME = "answer.txt"
# Stands in for the real path when a command is only being displayed, for `--explain`.
ANSWER_PLACEHOLDER = Path(f"<{ANSWER_FILENAME}>")
INSTALL_HINT = "Install Codex and sign in: https://developers.openai.com/codex/cli"


class CodexDriver:
    """No model is passed unless one is configured.

    This program will not guess at OpenAI's current small-model name: a wrong guess fails
    less clearly than codex's own default does.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        executable: str = "codex",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        home: Path | None = None,
        real_home: Path | None = None,
    ) -> None:
        self._model = model
        self._executable = executable
        self._timeout = timeout
        self._home = home if home is not None else paths.codex_home()
        self._real_home = real_home if real_home is not None else Path.home() / ".codex"

    @property
    def name(self) -> str:
        return f"codex ({self._model})" if self._model else "codex (default model)"

    def command_for(self, prompt: str, system_prompt: str) -> list[str]:
        return self._command(prompt, system_prompt, ANSWER_PLACEHOLDER)

    def complete(self, prompt: str, system_prompt: str) -> str:
        self._prepare_home()
        answer_file = self._home / ANSWER_FILENAME
        answer_file.unlink(missing_ok=True)

        command = self._command(prompt, system_prompt, answer_file)
        try:
            completed = subprocess.run(  # noqa: S603  # built here from constants, not input
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                stdin=subprocess.DEVNULL,
                env={**os.environ, "CODEX_HOME": str(self._home)},
                check=False,
            )
        except FileNotFoundError as exc:
            raise InferenceError(
                f"Could not run '{self._executable}': it is not on PATH.",
                remediation=INSTALL_HINT,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise InferenceError(
                f"'{self._executable}' did not answer within {self._timeout:.0f} seconds.",
                remediation="Check the CLI works interactively, or raise the timeout.",
            ) from exc

        if completed.returncode != 0:
            raise InferenceError(
                f"'{self._executable}' exited {completed.returncode}: "
                f"{completed.stderr.strip()[:MAX_ERROR_EXCERPT] or 'no output'}",
                remediation="Check the CLI is authenticated by running it interactively.",
            )

        if not answer_file.exists():
            raise InferenceError(
                f"'{self._executable}' produced no answer file.",
                remediation="Check the installed codex supports --output-last-message.",
            )
        return answer_file.read_text(encoding="utf-8")

    def _command(self, prompt: str, system_prompt: str, answer_file: Path) -> list[str]:
        # codex has no separate system prompt, so it is prepended to the user text.
        command = [self._executable, "exec", f"{system_prompt}\n\n{prompt}"]
        if self._model:
            command += ["--model", self._model]
        return [
            *command,
            "--sandbox",
            "read-only",
            # The scheduled run has no working directory to speak of, and this program is not
            # a code assistant: it must not care whether it is inside a repository.
            "--skip-git-repo-check",
            "--output-last-message",
            str(answer_file),
        ]

    def _prepare_home(self) -> None:
        """An isolated home with credentials symlinked in, and nothing else."""
        credentials = self._real_home / AUTH_FILENAME
        if not credentials.exists():
            raise InferenceError(
                f"codex is not signed in: {credentials} does not exist.",
                remediation="Run 'codex' once interactively and sign in, then try again.",
            )

        self._home.mkdir(parents=True, exist_ok=True)
        link = self._home / AUTH_FILENAME
        if link.is_symlink():
            if link.readlink() == credentials:
                return
            # The real home moved, so the link is stale and would fail confusingly.
            link.unlink()
        elif link.exists():
            raise InferenceError(
                f"{link} exists and is not a symlink.",
                remediation=(
                    "This program expects to manage that directory. Delete it and try again; "
                    "it holds only caches and a link to your real credentials."
                ),
            )

        link.symlink_to(credentials)
