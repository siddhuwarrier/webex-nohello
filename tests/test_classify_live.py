"""The classifier's decision boundary, against a real model.

Excluded from the default suite: these call `claude`, so they cost money and take seconds
each. Run them after changing the prompt, which is the thing most likely to regress
silently — the offline tests cover parsing and thresholds, but nothing else checks that the
prompt still means what it says.

    uv run pytest -m live -v

Article XIII.10 asks for this boundary to be pinned in both directions. The rows below are
the cases that actually caught prompt problems, not a sample of easy ones.
"""

from __future__ import annotations

import pytest

from conftest import make_message, make_person, make_space
from webex_nohello.models.classify.verdict_kind import VerdictKind
from webex_nohello.models.run.candidate import Candidate
from webex_nohello.models.webex.message import Message
from webex_nohello.services.agent_cli import build_driver
from webex_nohello.services.classify import ClassifierService

pytestmark = pytest.mark.live

ME = make_person(person_id="me", email="me@example.com")
THEM = make_person(person_id="them", email="them@example.com")


def exchange(*turns: tuple[str, str]) -> Candidate:
    """Build a conversation from (who, text) pairs, oldest first."""
    messages: list[Message] = [
        make_message(f"m{index}", sender=ME if who == "me" else THEM, text=text)
        for index, (who, text) in enumerate(turns, start=1)
    ]
    return Candidate(
        space=make_space("s1", "Them"), message=messages[-1], conversation=tuple(messages)
    )


@pytest.fixture(scope="module")
def classifier() -> ClassifierService:
    return ClassifierService(build_driver())


GREETINGS = [
    pytest.param(exchange(("them", "hi")), id="bare-hi"),
    pytest.param(exchange(("them", "good morning!")), id="good-morning"),
    pytest.param(exchange(("them", "hey Siddhu")), id="greeting-with-name"),
    pytest.param(exchange(("them", "you around?")), id="ping-only"),
    # Real cases the first prompt got wrong, both from colleagues sending a plain "Hi
    # Siddhu". A greeting is a conversation-opener and responds to no content, so earlier
    # messages must not turn it into continues_conversation, and must not depress the
    # confidence below the threshold either.
    pytest.param(exchange(("them", "Hi Siddhu")), id="greeting-with-name-mid-thread"),
    pytest.param(
        exchange(("me", "Sammmmmm"), ("them", "Hi Siddhu")),
        id="greeting-in-answer-to-being-pinged",
    ),
    pytest.param(
        exchange(("them", "did the deploy land?"), ("me", "yes"), ("them", "Hi Siddhu")),
        id="greeting-opening-a-new-topic",
    ),
]

NOT_GREETINGS = [
    pytest.param(
        exchange(
            ("them", "did the deploy land?"), ("me", "yes, an hour ago"), ("them", "lol nice")
        ),
        id="reaction-in-live-exchange",
    ),
    pytest.param(
        exchange(
            ("them", "can you check the logs?"), ("me", "done, all clear"), ("them", "thanks!")
        ),
        id="thanks-in-live-exchange",
    ),
    pytest.param(
        exchange(("them", "can you review PR 412 before standup?")),
        id="explicit-request",
    ),
    pytest.param(exchange(("them", "build is red")), id="short-but-actionable"),
    pytest.param(
        # A real misfire, observed in a different implementation that showed its classifier
        # only the last message. "Awesome" acknowledged a status update and got auto-replied
        # to. Kept verbatim: a case that caught a genuine bug beats an invented one.
        exchange(
            ("them", "I love this. I want it too, please."),
            (
                "me",
                "I am just creating a plugin for Webex integration (including this) and "
                "sending it to Devnet so they can publish it in the Claude Marketplace. "
                "Will share as soon as it's consumable for others (hopefully EOD).",
            ),
            ("them", "Awesome"),
        ),
        id="acknowledgement-of-a-status-update",
    ),
    pytest.param(
        exchange(("them", "hi"), ("them", "do you have the staging creds?")),
        id="greeting-then-question",
    ),
]


@pytest.mark.parametrize("candidate", GREETINGS)
def test_a_content_free_greeting_earns_a_reply(
    classifier: ClassifierService, candidate: Candidate
) -> None:
    assessment = classifier.assess(candidate, ME)

    assert assessment.verdict is not None, assessment.failure
    assert assessment.verdict.verdict is VerdictKind.GREETING_ONLY, assessment.summary
    assert assessment.is_reply_warranted, assessment.summary


@pytest.mark.parametrize("candidate", NOT_GREETINGS)
def test_anything_carrying_content_is_left_alone(
    classifier: ClassifierService, candidate: Candidate
) -> None:
    """The expensive direction to get wrong: a misfire here messages a real colleague."""
    assessment = classifier.assess(candidate, ME)

    assert assessment.verdict is not None, assessment.failure
    assert assessment.verdict.verdict is not VerdictKind.GREETING_ONLY, assessment.summary
    assert not assessment.is_reply_warranted, assessment.summary


def test_the_threshold_covers_a_reaction_stripped_of_its_context(
    classifier: ClassifierService,
) -> None:
    """Belt and braces behind Article IX.2.

    Shown "Awesome" with no exchange around it, the model does call it a greeting — which is
    how the real misfire happened elsewhere. Context is the primary defence; the confidence
    floor is the second, and this pins that the second one holds on its own.
    """
    stranded = exchange(("them", "Awesome"))

    assessment = classifier.assess(stranded, ME)

    assert assessment.verdict is not None, assessment.failure
    assert not assessment.is_reply_warranted, assessment.summary
