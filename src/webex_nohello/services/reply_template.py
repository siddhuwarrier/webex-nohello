"""The reply itself: where its text is read from, and how it is rendered.

The shipped default is Appendix B of the constitution, verbatim. An operator replaces it by
writing a Markdown file — `reply.md` beside their config by default, or whatever `reply_file`
in the config points at. The placeholders they may use, and the rule that an unknown one is
an error, live in models/reply/reply_placeholder.py.
"""

from __future__ import annotations

from pathlib import Path

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError
from webex_nohello.models.reply.reply_placeholder import ReplyPlaceholder
from webex_nohello.models.reply.reply_source import ReplySource
from webex_nohello.models.webex.person import Person

DEFAULT_TEMPLATE = """\
👋 Automated reply — apologies, this message won't reach me as it stands.

I'm sorry to answer with automation rather than a proper reply. Your message looks like a \
greeting on its own, so it has been marked as read automatically and won't stay in my \
unread list. I won't see it unless you follow up with what you actually need.

I really don't mean that as a brush-off. I work asynchronously across timezones, and it \
genuinely helps if a first message carries enough for me to act on: the question, any \
relevant context, and any links. Then I can give you a proper answer the first time I read \
it, rather than the two of us trading hellos.

The reasoning, put rather better than I can manage: https://nohello.net/en/

Do send the details whenever suits you and I'll pick it up from there — and thank you for \
bearing with the automation.\
"""


def locate(configured: Path | None, *, default_path: Path) -> Path:
    """Which file holds the reply text.

    `~` is expanded and a relative path is taken as relative to the config file's own
    directory: a config that could only say `/Users/someone/reply.md` would break the moment
    it was copied to another machine.
    """
    if configured is None:
        return default_path
    expanded = configured.expanduser()
    return expanded if expanded.is_absolute() else default_path.parent / expanded


def load_reply(configured: Path | None, *, default_path: Path) -> ReplySource:
    """Read the reply text, and report which file it came from.

    A missing file means different things either side of `reply_file`. Unset, it simply means
    the operator has not written their own yet, so the shipped default stands. Set, it means
    they pointed at something that is not there — and quietly sending the default instead
    would put words in their mouth they did not write.
    """
    path = locate(configured, default_path=default_path)

    if not path.exists():
        if configured is not None:
            raise WebexNoHelloError(
                f"reply_file points at {path}, which does not exist.",
                remediation=(
                    "Create it, or run 'webex-nohello config template' to write the default "
                    "there, or remove reply_file to use the built-in text."
                ),
            )
        return ReplySource(text=DEFAULT_TEMPLATE, path=path, is_customised=False)

    return ReplySource(text=_read(path), path=path, is_customised=True)


def _read(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WebexNoHelloError(
            f"Could not read the reply text at {path}: {exc}",
            remediation="Check the file's permissions, or delete it to use the default.",
        ) from exc

    if not text.strip():
        raise WebexNoHelloError(
            f"The reply text at {path} is empty.",
            remediation="Write something, or delete the file to use the default.",
        )
    return text


def render(template: str, sender: Person) -> str:
    """Substitute the supported placeholders, refusing anything else.

    Settings already rejects an unknown placeholder when the config is read. This checks
    again on the path that actually sends, because the cost of the two disagreeing is a
    colleague receiving a half-rendered message.
    """
    try:
        unknown = ReplyPlaceholder.unknown_in(template)
    except ValueError as exc:
        raise WebexNoHelloError(
            f"The reply text is not a valid template: {exc}",
            remediation="Correct reply_text in your config file.",
        ) from exc

    if unknown:
        raise WebexNoHelloError(
            f"The reply text uses unknown placeholder(s): {', '.join(unknown)}",
            remediation=f"Available placeholders are: {', '.join(ReplyPlaceholder.names())}",
        )

    return template.format(**_placeholders_for(sender))


def _placeholders_for(sender: Person) -> dict[str, str]:
    display = sender.display_name.strip()
    return {
        "sender_display_name": display,
        # Best effort, and only ever used if the operator asks for it: a display name is not
        # reliably "first last", so this is the leading word or nothing.
        "sender_first_name": display.split()[0] if display else "",
        "sender_email": sender.primary_email,
    }
