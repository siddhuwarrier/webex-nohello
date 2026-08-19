"""Why a space yielded no candidate.

Recorded per space so that a run finding nothing can say why, space by space. "Nothing to
do" and "I could not tell" look identical without this, and Article IV requires an
engineer be able to work out what happened.
"""

from __future__ import annotations

from enum import StrEnum


class SkipReason(StrEnum):
    NO_MESSAGES = "no_messages"
    NOTHING_NEW = "nothing_new"
    LATEST_IS_MINE = "latest_is_mine"
    SENDER_IS_NOT_HUMAN = "sender_is_not_human"
    NO_TEXT = "no_text"

    @property
    def description(self) -> str:
        return _DESCRIPTIONS[self]


_DESCRIPTIONS = {
    SkipReason.NO_MESSAGES: "the space is empty",
    SkipReason.NOTHING_NEW: "nothing new since the last run",
    SkipReason.LATEST_IS_MINE: "you sent the most recent message",
    SkipReason.SENDER_IS_NOT_HUMAN: "the most recent message is from a bot",
    SkipReason.NO_TEXT: "the most recent message has no text",
}
