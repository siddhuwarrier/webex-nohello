"""Choosing which agent CLI classifies.

Article III.2: inference goes through whichever CLI the operator already has, so no separate
LLM API key is needed. Article III.3: one module per CLI, and nothing outside them knows
which is in use — this module holds no invocation logic at all.

`claude` is preferred when both are present. Not a judgement about the models: `claude`
supports `--system-prompt` and `--strict-mcp-config`, so its context is a tenth the size and
Article IX.4's "no tools" is satisfied directly rather than by isolating a home directory.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable

from webex_nohello.services.claude_cli import ClaudeDriver
from webex_nohello.services.codex_cli import CodexDriver
from webex_nohello.services.inference import (
    DEFAULT_TIMEOUT_SECONDS,
    InferenceDriver,
    InferenceError,
)

AUTO = "auto"
CLAUDE = "claude"
CODEX = "codex"
CHOICES = (AUTO, CLAUDE, CODEX)

# Injectable so the suite can decide what is installed. Article XIII.8 requires the tests run
# with no agent CLI present at all, which is exactly the case CI runs in and a developer
# machine never does.
type Availability = Callable[[str], bool]


def _on_path(executable: str) -> bool:
    return shutil.which(executable) is not None


def build_driver(
    *,
    preference: str = AUTO,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    is_installed: Availability = _on_path,
) -> InferenceDriver:
    if preference == CLAUDE:
        return _require(CLAUDE, ClaudeDriver(model=model, timeout=timeout), is_installed)
    if preference == CODEX:
        return _require(CODEX, CodexDriver(model=model, timeout=timeout), is_installed)
    if preference != AUTO:
        raise InferenceError(
            f"Unknown classifier {preference!r}.",
            remediation=f"Choose one of: {', '.join(CHOICES)}.",
        )

    if is_installed(CLAUDE):
        return ClaudeDriver(model=model, timeout=timeout)
    if is_installed(CODEX):
        return CodexDriver(model=model, timeout=timeout)

    raise InferenceError(
        "Neither 'claude' nor 'codex' is on PATH, so messages cannot be classified.",
        remediation=(
            "Install one and sign in. Claude Code: https://docs.claude.com/en/docs/claude-code "
            "-- Codex: https://developers.openai.com/codex/cli"
        ),
    )


def installed_classifiers(is_installed: Availability = _on_path) -> tuple[str, ...]:
    """Which CLIs are present, in preference order.

    Separate from `build_driver` because a caller sometimes wants to *report* the situation
    rather than act on it — `auth login` says which will be used, and must not fail merely
    because neither is installed yet.
    """
    return tuple(name for name in (CLAUDE, CODEX) if is_installed(name))


def _require(
    executable: str, driver: InferenceDriver, is_installed: Availability
) -> InferenceDriver:
    """Fail now, with a clear reason, rather than on the first classification."""
    if is_installed(executable):
        return driver
    raise InferenceError(
        f"'{executable}' was chosen as the classifier but is not on PATH.",
        remediation=(
            f"Install {executable}, or set `classifier` to the other one in your config. "
            "A scheduled run gets a minimal PATH, so reinstall the schedule afterwards."
        ),
    )
