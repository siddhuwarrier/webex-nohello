"""The placeholders an operator's reply text may use.

Named here rather than in the service that substitutes them, because the settings model
validates a configured reply against this set the moment the config file is read — and a
model must not import a service.

Article XI.4: an unknown placeholder is an error. A reply that reached a colleague reading
"Hi {their_name}" literally would be worse than no reply at all.
"""

from __future__ import annotations

from enum import StrEnum
from string import Formatter


class ReplyPlaceholder(StrEnum):
    """Each member's value is the name written between braces in the reply text."""

    SENDER_FIRST_NAME = "sender_first_name"
    SENDER_DISPLAY_NAME = "sender_display_name"
    SENDER_EMAIL = "sender_email"

    @classmethod
    def unknown_in(cls, text: str) -> tuple[str, ...]:
        """The placeholder names in `text` that this program cannot substitute.

        Raises:
            ValueError: if `text` is not a valid format string at all. Deliberately the
                same exception type a Pydantic validator expects, so the offending config
                key gets named rather than a traceback reaching the operator.
        """
        try:
            requested = {field for _, field, _, _ in Formatter().parse(text) if field}
        except ValueError as exc:
            hint = "Write a literal brace as {{ or }}."
            raise ValueError(f"{exc}. {hint}") from exc

        known = {member.value for member in cls}
        return tuple(sorted(requested - known))

    @classmethod
    def names(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)
