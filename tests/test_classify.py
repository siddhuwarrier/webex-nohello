"""ClassifierService: parsing the model's answer, and the threshold that gates a reply.

The suite runs offline — the driver is faked, so no CLI is invoked.
"""

from __future__ import annotations

import json

import pytest

from conftest import FakeInferenceDriver, make_candidate, make_person
from webex_nohello.models.classify.assessment import Assessment
from webex_nohello.models.classify.verdict_kind import VerdictKind
from webex_nohello.services.classify import DEFAULT_CONFIDENCE_THRESHOLD, ClassifierService

OPERATOR = make_person(person_id="me", email="me@example.com")
SENDER = make_person(person_id="them", email="them@example.com")


def answer(
    kind: str = "greeting_only", confidence: float = 0.95, reason: str = "bare hello"
) -> str:
    return json.dumps({"verdict": kind, "confidence": confidence, "reason": reason})


def assess(
    driver: FakeInferenceDriver, *, text: str = "hi", threshold: float | None = None
) -> Assessment:
    service = ClassifierService(
        driver,
        confidence_threshold=(threshold if threshold is not None else DEFAULT_CONFIDENCE_THRESHOLD),
    )
    return service.assess(make_candidate(text=text, sender=SENDER), OPERATOR)


class TestParsing:
    def test_a_plain_json_answer_is_read(self) -> None:
        result = assess(FakeInferenceDriver([answer()]))

        assert result.verdict is not None
        assert result.verdict.verdict is VerdictKind.GREETING_ONLY
        assert result.verdict.confidence == pytest.approx(0.95)
        assert result.verdict.reason == "bare hello"

    def test_a_fenced_answer_is_read(self) -> None:
        """Models fence JSON even when told not to, so the fence is stripped, not punished."""
        result = assess(FakeInferenceDriver([f"```json\n{answer()}\n```"]))

        assert result.verdict is not None
        assert result.verdict.verdict is VerdictKind.GREETING_ONLY

    def test_a_fence_without_a_language_is_read(self) -> None:
        result = assess(FakeInferenceDriver([f"```\n{answer()}\n```"]))

        assert result.verdict is not None

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        result = assess(FakeInferenceDriver([f"\n\n  {answer()}  \n"]))

        assert result.verdict is not None

    def test_an_unknown_extra_key_is_tolerated(self) -> None:
        payload = json.dumps(
            {"verdict": "greeting_only", "confidence": 0.9, "reason": "r", "extra": 1}
        )

        result = assess(FakeInferenceDriver([payload]))

        assert result.verdict is not None

    def test_prose_around_the_json_is_a_failure_not_a_guess(self) -> None:
        """Article V.4: never guess. A wrong guess here posts a message to a colleague."""
        driver = FakeInferenceDriver(["Sure! Here is my answer: " + answer()] * 2)

        result = assess(driver)

        assert result.verdict is None
        assert result.failure is not None
        assert not result.is_reply_warranted

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "not json at all",
            "{}",
            json.dumps({"verdict": "made_up", "confidence": 0.9, "reason": "r"}),
            json.dumps({"verdict": "greeting_only", "confidence": 1.4, "reason": "r"}),
            json.dumps({"verdict": "greeting_only", "confidence": -0.1, "reason": "r"}),
            json.dumps({"verdict": "greeting_only", "confidence": 0.9, "reason": ""}),
            json.dumps({"verdict": "greeting_only", "reason": "r"}),
            json.dumps(["greeting_only", 0.9]),
        ],
    )
    def test_a_malformed_answer_never_yields_a_verdict(self, bad: str) -> None:
        result = assess(FakeInferenceDriver([bad, bad]))

        assert result.verdict is None
        assert not result.is_reply_warranted


class TestRetry:
    def test_a_second_attempt_is_made_after_an_unparseable_answer(self) -> None:
        driver = FakeInferenceDriver(["rubbish", answer()])

        result = assess(driver)

        assert result.verdict is not None
        assert driver.call_count == 2

    def test_the_retry_asks_more_firmly(self) -> None:
        driver = FakeInferenceDriver(["rubbish", answer()])

        assess(driver)

        assert "could not be parsed" in driver.prompts[1]
        assert "could not be parsed" not in driver.prompts[0]

    def test_retries_are_bounded(self) -> None:
        """Retrying forever would spend real money on a prompt that needs fixing."""
        driver = FakeInferenceDriver(["rubbish"] * 10)

        result = assess(driver)

        assert driver.call_count == 2
        assert result.failure is not None

    def test_a_driver_failure_is_not_retried(self) -> None:
        """A missing or unauthenticated CLI fails identically every time."""
        driver = FakeInferenceDriver([], fail_with="claude is not on PATH")

        result = assess(driver)

        assert driver.call_count == 1
        assert result.failure == "claude is not on PATH"


class TestThreshold:
    def test_a_confident_greeting_warrants_a_reply(self) -> None:
        result = assess(FakeInferenceDriver([answer(confidence=0.95)]))

        assert result.is_reply_warranted

    def test_confidence_below_the_threshold_does_not(self) -> None:
        """Article IX.8: ambiguity resolves to silence."""
        result = assess(FakeInferenceDriver([answer(confidence=0.6)]))

        assert result.verdict is not None
        assert not result.is_reply_warranted

    def test_the_threshold_boundary_is_inclusive(self) -> None:
        result = assess(FakeInferenceDriver([answer(confidence=0.8)]), threshold=0.8)

        assert result.is_reply_warranted

    @pytest.mark.parametrize("kind", ["has_request", "continues_conversation"])
    def test_only_a_greeting_can_warrant_a_reply(self, kind: str) -> None:
        """However confident the model is, the other two verdicts never send anything."""
        result = assess(FakeInferenceDriver([answer(kind=kind, confidence=1.0)]))

        assert result.verdict is not None
        assert not result.is_reply_warranted


class TestPrefilter:
    def test_a_long_message_skips_inference_entirely(self) -> None:
        driver = FakeInferenceDriver([answer()])
        long_message = " ".join(["word"] * 40)

        result = assess(driver, text=long_message)

        assert result.was_prefiltered
        assert driver.call_count == 0

    def test_a_prefiltered_message_is_never_replied_to(self) -> None:
        """Article IX.1: the pre-filter may only skip. It can never cause a reply."""
        result = assess(FakeInferenceDriver([answer()]), text=" ".join(["word"] * 40))

        assert not result.is_reply_warranted
        assert result.verdict is None

    def test_a_short_message_still_goes_to_the_model(self) -> None:
        driver = FakeInferenceDriver([answer()])

        assess(driver, text="hi")

        assert driver.call_count == 1


class TestExplainability:
    def test_the_prompt_can_be_produced_without_calling_the_model(self) -> None:
        """Article IX.10: a verdict must be reproducible by hand."""
        service = ClassifierService(FakeInferenceDriver([]))
        candidate = make_candidate(text="hi", sender=SENDER)

        prompt = service.prompt_for(candidate, OPERATOR)

        assert "hi" in prompt
        assert "greeting_only" in prompt

    def test_the_command_can_be_produced_without_calling_the_model(self) -> None:
        service = ClassifierService(FakeInferenceDriver([]))
        candidate = make_candidate(text="hi", sender=SENDER)

        command = service.command_for(candidate, OPERATOR)

        assert command[0] == "fake-cli"
