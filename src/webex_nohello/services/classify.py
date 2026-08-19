"""Deciding whether a candidate is a content-free greeting.

Article IX. The service owns three things the driver does not: the confidence threshold,
the bounded retry when the model answers with something unparseable, and the local
pre-filter that avoids paying for inference on messages that plainly are not greetings.

An unparseable answer costs that one candidate its classification and nothing more
(Article V.4). It is never guessed at, and it never aborts the run.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from webex_nohello.models.classify.assessment import Assessment
from webex_nohello.models.classify.verdict import Verdict
from webex_nohello.models.run.candidate import Candidate
from webex_nohello.models.webex.person import Person
from webex_nohello.services.classification_prompt import (
    SYSTEM_PROMPT,
    build_prompt,
    is_obviously_substantial,
)
from webex_nohello.services.inference import InferenceDriver, InferenceError

# Article IX.8: ambiguity resolves to silence.
DEFAULT_CONFIDENCE_THRESHOLD = 0.8

# One retry. The failure mode is a model wrapping JSON in prose, which a second attempt
# usually fixes; more than that is throwing money at a prompt that needs changing.
DEFAULT_ATTEMPTS = 2

_FENCE = re.compile(r"^\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*$", re.DOTALL)
_RETRY_SUFFIX = (
    "\n\nYour previous answer could not be parsed. Reply with the JSON object alone: "
    "no prose, no code fence, no explanation outside the JSON."
)


class ClassifierService:
    def __init__(
        self,
        driver: InferenceDriver,
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        attempts: int = DEFAULT_ATTEMPTS,
    ) -> None:
        self._driver = driver
        self._threshold = confidence_threshold
        self._attempts = attempts

    @property
    def driver_name(self) -> str:
        return self._driver.name

    def prompt_for(self, candidate: Candidate, operator: Person) -> str:
        """Exposed so `--explain` can show exactly what the model was asked."""
        return build_prompt(candidate, operator)

    def command_for(self, candidate: Candidate, operator: Person) -> list[str]:
        """Exposed so `--explain` can show the exact command, per Article IX.10."""
        return self._driver.command_for(self.prompt_for(candidate, operator), SYSTEM_PROMPT)

    def assess(self, candidate: Candidate, operator: Person) -> Assessment:
        if is_obviously_substantial(candidate.message):
            # Article IX.1: the pre-filter may only skip. It cannot cause a reply.
            return Assessment(candidate=candidate, was_prefiltered=True)

        prompt = build_prompt(candidate, operator)
        last_failure = "no attempt was made"

        for attempt in range(1, self._attempts + 1):
            asked = prompt if attempt == 1 else prompt + _RETRY_SUFFIX
            try:
                answer = self._driver.complete(asked, SYSTEM_PROMPT)
            except InferenceError as exc:
                # A CLI that is missing, unauthenticated or timing out will fail the same
                # way for every candidate, so there is nothing to gain from retrying.
                return Assessment(candidate=candidate, failure=exc.message)

            verdict = _parse(answer)
            if verdict is not None:
                return Assessment(
                    candidate=candidate,
                    verdict=verdict,
                    is_reply_warranted=(
                        verdict.verdict.is_replyable
                        and verdict.is_confident_enough(self._threshold)
                    ),
                )
            last_failure = f"unparseable answer: {_excerpt(answer)}"

        return Assessment(candidate=candidate, failure=last_failure)


def _parse(answer: str) -> Verdict | None:
    """Read a verdict out of the model's text, or None if it cannot be read.

    Models wrap JSON in a code fence even when told not to, so the fence is stripped rather
    than treated as a failure. Anything beyond that is a genuine failure, not something to
    guess around.
    """
    body = answer.strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group("body").strip()

    try:
        payload = json.loads(body)
    except ValueError:
        return None

    try:
        return Verdict.model_validate(payload)
    except ValidationError:
        return None


def _excerpt(answer: str) -> str:
    collapsed = " ".join(answer.split())
    return collapsed[:200] if collapsed else "(empty)"
