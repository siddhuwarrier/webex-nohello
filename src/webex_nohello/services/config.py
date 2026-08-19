"""Loading settings from the operator's config file.

Absent means defaults (Article XI.1). Present but wrong means refusing to run, naming the
offending key — a program that posts as the operator must not start on a silently coerced
value (Article XI.5).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import ValidationError

from webex_nohello.models.config.settings import Settings
from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError

STARTER_CONFIG = """\
# webex-nohello configuration.
#
# Every setting below is shown at its default. Delete this file at any time to go back to
# those defaults. An unrecognised key is an error rather than being ignored, so a typo is
# reported rather than silently doing nothing.

# Replies are sent from your own Webex account and cannot be unsent, so by default this
# program replies to NOBODY until you list them below. Set this to false to reply to anyone
# who is not in deny_list -- but read the whole file first.
opt_in_only = true

# Who may receive a reply. Only consulted while opt_in_only is true.
# Start with your own address, or one willing colleague.
allow_list = []

# Who never receives one. Consulted always, and wins over allow_list.
deny_list = []

# At most one reply per person per this many minutes.
#
# Deliberately short. Someone who keeps sending content-free greetings keeps earning the
# nudge, so this is not a "once a month" limit. It exists so that a burst within a single
# interaction -- "hi" ... "hello?" ... "you there?" -- draws one reply rather than three.
# Set to 0 to reply every single time.
cooldown_minutes = 30

# Stop after this many replies in a single run. Wanting to exceed it is treated as a fault
# rather than a limit: it usually means the read positions are wrong, not that you really
# had that many greetings.
max_replies_per_run = 5

# How sure the classifier must be before a reply is considered warranted. Raising this
# sends fewer replies and misses more greetings; lowering it does the opposite. Anything
# below about 0.7 starts replying to things that merely look like greetings.
confidence_threshold = 0.8

# Which agent CLI decides whether a message is a bare greeting: "auto", "claude" or "codex".
#
# "auto" uses claude when it is installed, otherwise codex. That preference is not a
# judgement about the models: claude can be told directly to expose no tools, whereas codex
# has to be pointed at an isolated home directory to achieve the same thing.
classifier = "auto"

# Which model that CLI should use. Leave it out to accept each CLI's own default -- `haiku`
# for claude, and whatever your codex is configured with.
# classifier_model = "haiku"

# Which file holds the message that gets sent.
#
# The reply is prose, so it lives in a Markdown file rather than in here. Leave this out and
# it is read from reply.md beside this file -- and if that does not exist either, the
# built-in default is sent. Point it somewhere else to keep your wording with your own
# notes; a relative path is relative to this file's directory, and ~ works.
#
#   webex-nohello config reply       shows the text in force and which file it came from
#   webex-nohello config template    writes the default into that file so you can edit it
#
# Three placeholders are available: {sender_first_name}, {sender_display_name} and
# {sender_email}. Anything else in braces is an error rather than being sent literally.
#
# reply_file = "reply.md"
"""


def write_starter_config(path: Path, *, overwrite: bool = False) -> bool:
    """Write the commented starter config. Returns False if one was already there.

    Never overwrites without being asked: the file may name real colleagues, and losing an
    allow_list silently would be a poor way to find out.
    """
    if path.exists() and not overwrite:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STARTER_CONFIG, encoding="utf-8")
    return True


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WebexNoHelloError(
            f"Could not read the config file at {path}: {exc}",
            remediation="Check the file's permissions.",
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise WebexNoHelloError(
            f"The config file at {path} is not valid TOML: {exc}",
            remediation="Fix the syntax, or delete the file to fall back to defaults.",
        ) from exc

    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        raise WebexNoHelloError(
            f"The config file at {path} has a problem:\n{_describe(exc)}",
            remediation=(
                "Correct the key, or delete the file to fall back to defaults. Note that "
                "defaults reply to nobody until you add someone to allow_list."
            ),
        ) from exc


def _describe(error: ValidationError) -> str:
    """Name the offending key, which is the only part the operator can act on."""
    lines = []
    for problem in error.errors():
        location = ".".join(str(part) for part in problem["loc"]) or "(top level)"
        lines.append(f"  {location}: {problem['msg']}")
    return "\n".join(lines)
