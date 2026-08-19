"""The walkthrough is the whole of first-run onboarding, so its content is asserted."""

from __future__ import annotations

from webex_nohello.commands.registration_guide import (
    PORTAL_URL,
    SUGGESTED_DESCRIPTION,
    SUGGESTED_NAME,
    registration_steps,
)
from webex_nohello.scopes import REQUIRED_SCOPES

CALLBACK = "http://localhost:8090/callback"


def test_every_step_explains_itself() -> None:
    for step in registration_steps(CALLBACK):
        assert step.title.strip()
        assert step.detail.strip()


def test_the_callback_url_is_offered_verbatim_for_pasting() -> None:
    """It must match character for character, so it cannot be paraphrased."""
    pasteable = [
        field.value for step in registration_steps(CALLBACK) for field in step.values_to_copy
    ]

    assert CALLBACK in pasteable


def test_the_portal_url_is_offered() -> None:
    pasteable = [
        field.value for step in registration_steps(CALLBACK) for field in step.values_to_copy
    ]

    assert PORTAL_URL in pasteable


def test_a_name_and_description_are_supplied_so_the_operator_invents_nothing() -> None:
    labelled = {
        field.label: field.value
        for step in registration_steps(CALLBACK)
        for field in step.values_to_copy
    }

    assert labelled["Name"] == SUGGESTED_NAME
    assert labelled["Description"] == SUGGESTED_DESCRIPTION


def test_the_suggested_description_says_what_the_tool_does() -> None:
    lowered = SUGGESTED_DESCRIPTION.lower()

    assert "nohello.net" in lowered
    assert "greeting" in lowered


def test_every_scope_is_listed_with_its_reason() -> None:
    bullets = [bullet for step in registration_steps(CALLBACK) for bullet in step.bullets]

    for scope in REQUIRED_SCOPES:
        assert any(scope.name in bullet and scope.reason in bullet for bullet in bullets)


def test_the_scope_step_lists_nothing_beyond_what_is_required() -> None:
    """Article VIII.3: the operator is told exactly what is asked for, and no more."""
    bullets = [bullet for step in registration_steps(CALLBACK) for bullet in step.bullets]

    assert len(bullets) == len(REQUIRED_SCOPES)


def test_the_operator_is_warned_the_secret_is_shown_once() -> None:
    details = " ".join(step.detail for step in registration_steps(CALLBACK))

    assert "once" in details


def test_the_steps_are_in_a_workable_order() -> None:
    """Credentials come last: they do not exist until the integration is created."""
    titles = [step.title.lower() for step in registration_steps(CALLBACK)]

    assert "open" in titles[0]
    assert "client id" in titles[-1]
