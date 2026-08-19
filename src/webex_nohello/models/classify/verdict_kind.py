"""What the classifier decided a message is.

Three kinds rather than a boolean, because the two ways of *not* warranting a reply are
different and the distinction is what Article IX.6 turns on. "lol" carries no request but
is content-bearing in context; the same word arriving cold is not.
"""

from __future__ import annotations

from enum import StrEnum


class VerdictKind(StrEnum):
    # A bare greeting or ping, with no request, not continuing anything. The only kind
    # that may earn a reply.
    GREETING_ONLY = "greeting_only"
    # Carries a question, a task, or information to act on.
    HAS_REQUEST = "has_request"
    # Short, but a natural response within a live exchange.
    CONTINUES_CONVERSATION = "continues_conversation"

    @property
    def is_replyable(self) -> bool:
        return self is VerdictKind.GREETING_ONLY
