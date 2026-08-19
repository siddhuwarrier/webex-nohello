"""Where this program keeps its files. Injectable, so tests never touch a real directory."""

from __future__ import annotations

from pathlib import Path

import platformdirs

APP_NAME = "webex-nohello"
SCAN_STATE_FILENAME = "scan-state.json"
AUDIT_LOG_FILENAME = "replies.jsonl"
LOCK_FILENAME = "run.lock"
PAUSED_FILENAME = "PAUSED"
RUN_LOG_FILENAME = "run.log"
CODEX_HOME_DIRNAME = "codex-home"
CONFIG_FILENAME = "config.toml"
TEMPLATE_FILENAME = "reply.md"


def state_directory() -> Path:
    return Path(platformdirs.user_state_dir(APP_NAME))


def config_directory() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME))


def scan_state_file() -> Path:
    return state_directory() / SCAN_STATE_FILENAME


def audit_log_file() -> Path:
    """Append-only record of every reply attempted. Also the source of cooldowns."""
    return state_directory() / AUDIT_LOG_FILENAME


def lock_file() -> Path:
    return state_directory() / LOCK_FILENAME


def paused_file() -> Path:
    """The kill switch of Article X.6. Its mere existence stops every run."""
    return state_directory() / PAUSED_FILENAME


def run_log_file() -> Path:
    """Where a scheduled run's output goes. Nobody is watching the terminal for those."""
    return state_directory() / RUN_LOG_FILENAME


def codex_home() -> Path:
    """An isolated CODEX_HOME, so codex loads none of the operator's plugins.

    See services/codex_cli.py: plugins are how codex acquires MCP servers, and there is no
    flag to disable them. Reused rather than temporary, because a cold home costs about 17
    seconds.
    """
    return state_directory() / CODEX_HOME_DIRNAME


def config_file() -> Path:
    return config_directory() / CONFIG_FILENAME


def reply_template_file() -> Path:
    """Where the reply text lives unless `reply_file` in the config points elsewhere."""
    return config_directory() / TEMPLATE_FILENAME
