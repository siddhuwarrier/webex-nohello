"""The classifier's prompt.

Kept in one place, as prose, so it can be reviewed as prose (Article III.6). The system
prompt is a byte-stable constant on purpose: the agent CLI caches on the prefix, so
varying it would pay full price on every call.

Article IX.6 is the rule this text exists to encode, and it is the easy thing to get
wrong: brevity is not the test. "lol" inside a live exchange is a perfectly good reply and
must be left alone; the same word arriving out of nowhere is not.
"""

from __future__ import annotations

from webex_nohello.models.run.candidate import Candidate
from webex_nohello.models.webex.message import Message
from webex_nohello.models.webex.person import Person

SYSTEM_PROMPT = (
    "You classify the final message of a Webex direct-message conversation. "
    "You reply with a single JSON object and nothing else: no prose, no code fence."
)

# A message this long is plainly not a bare greeting, so it is ruled out locally rather
# than paid for. Article IX.1 permits this only as a skip: it can never cause a reply.
PREFILTER_WORD_LIMIT = 15

_INSTRUCTIONS = """\
Decide what the FINAL message in the conversation below is.

Answer with this JSON object exactly:
{"verdict": "<one of the three below>", "confidence": <number between 0 and 1>, \
"reason": "<one short sentence>"}

The three verdicts:

  greeting_only
      A greeting, ping, or pleasantry that asks for nothing and answers nothing, so the
      recipient is left with nothing to act on. Examples: "hi", "hello", "hi <name>",
      "good morning", "hey", "you there?", "got a minute?", "are you free?".

      This applies even when earlier messages exist. A greeting is a conversation
      OPENER. Someone saying "Hi <name>" is opening a conversation whether or not they
      have spoken to you before, whether or not it follows a gap, and whether or not
      they are responding to you calling their name. None of that gives the recipient
      anything to act on, so all of it is still greeting_only.

  has_request
      Contains a question, a task, a decision to make, or information to act on --
      however short. "can you review my PR" is a request. So is "the build is red".

  continues_conversation
      A message that responds to something SPECIFIC in the messages before it: an
      acknowledgement, a reaction, or an answer. "lol", "thanks", "nice", "ok",
      "sounds good", "will do", "yes please".

The test that separates the last two, and it is the one to apply:

  Does the message depend on the CONTENT of what came before it?

  "lol" depends on it -- it is meaningless without knowing what was funny. "thanks"
  depends on it. "yes" depends on it. Those are continues_conversation.

  A greeting does not depend on it. "Hi <name>" reads exactly the same in isolation as
  it does after twenty messages, because it responds to no content at all. That is
  greeting_only, wherever it appears.

  Being preceded by your own message does not make a greeting into a response. If you
  said "<name>?" and they replied "Hi <name>", they have greeted you, not answered you.

On confidence:

  Be decisive when the message clearly falls into one category. A plain greeting, with
  or without a name, with or without earlier messages, is a clear greeting_only:
  answer it with high confidence. Do not lower your confidence merely because earlier
  messages exist.

  Reserve low confidence for messages that genuinely combine a greeting with something
  to act on, or where you truly cannot tell whether a short message is reacting to
  specific content.

  A reply is sent only for greeting_only at high confidence, and it is a real message to
  a colleague -- so do not guess. But hedging on a plain greeting is also a failure: it
  is the case this exists to catch.

CONVERSATION (oldest first; the final entry is the one to classify):
"""


def is_obviously_substantial(message: Message) -> bool:
    """A cheap local check that can only ever skip inference, never trigger a reply."""
    return len(message.text.split()) > PREFILTER_WORD_LIMIT


def build_prompt(candidate: Candidate, operator: Person) -> str:
    """The full user prompt: the instructions, then the exchange."""
    lines = [
        _render(message, operator, index)
        for index, message in enumerate(candidate.conversation, start=1)
    ]
    return _INSTRUCTIONS + "\n".join(lines) + "\n"


def _render(message: Message, operator: Person, index: int) -> str:
    """One conversation line: who, when, and what.

    The operator is labelled "me" rather than by name, because the rule turns on whether
    the exchange is two-sided and that is easier to see when the sides are unambiguous.
    """
    author = "me" if message.person_id == operator.id else (message.person_email or "them")
    when = message.created.strftime("%Y-%m-%d %H:%M") if message.created else "unknown time"
    text = " ".join(message.text.split()) or "(no text)"
    return f"[{index}] {author} ({when}): {text}"
