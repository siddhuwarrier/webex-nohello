"""A message that might be a content-free greeting, with the context needed to judge it.

The conversation is what makes Article IX.6 decidable: "lol" inside a live exchange is
content-bearing, whereas the same word arriving cold is not. A classifier shown only the
latest message cannot tell those apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from webex_nohello.models.webex.message import Message
from webex_nohello.models.webex.space import Space


@dataclass(frozen=True)
class Candidate:
    space: Space
    message: Message
    # Oldest first, including `message` as the final entry (Article IX.2).
    conversation: tuple[Message, ...]

    @property
    def sender_email(self) -> str:
        return self.message.person_email

    @property
    def is_first_contact(self) -> bool:
        """Whether the candidate is the only message in the space.

        Not decisive on its own, but a lone opening greeting is the clearest possible
        case, and it is worth being able to say so in the dry-run report.
        """
        return len(self.conversation) == 1
