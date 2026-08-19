"""The reply itself.

The shipped default is Appendix B of the constitution, verbatim. An operator may replace it
with a file of their own; Article XI.4 requires an unknown placeholder be an error rather
than rendering blank, because a reply reading "Hi {their_name}" literally would be worse
than no reply at all.
"""

from __future__ import annotations

from pathlib import Path
from string import Formatter

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError
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


def load_template(path: Path) -> str:
    if not path.exists():
        return DEFAULT_TEMPLATE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WebexNoHelloError(
            f"Could not read the reply template at {path}: {exc}",
            remediation="Check the file's permissions, or delete it to use the default.",
        ) from exc

    if not text.strip():
        raise WebexNoHelloError(
            f"The reply template at {path} is empty.",
            remediation="Write something, or delete the file to use the default.",
        )
    return text


def render(template: str, sender: Person) -> str:
    """Substitute the supported placeholders, refusing anything else."""
    available = _placeholders_for(sender)
    requested = {
        field for _, field, _, _ in Formatter().parse(template) if field is not None and field
    }

    unknown = sorted(requested - available.keys())
    if unknown:
        raise WebexNoHelloError(
            f"The reply template uses unknown placeholder(s): {', '.join(unknown)}",
            remediation=f"Available placeholders are: {', '.join(sorted(available))}",
        )

    return template.format(**available)


def _placeholders_for(sender: Person) -> dict[str, str]:
    display = sender.display_name.strip()
    return {
        "sender_display_name": display,
        # Best effort, and only ever used if the operator asks for it: a display name is not
        # reliably "first last", so this is the leading word or nothing.
        "sender_first_name": display.split()[0] if display else "",
        "sender_email": sender.primary_email,
    }
