"""Interpreting the OAuth redirect, and the remediation each failure earns.

This is the security-relevant half of sign-in: the `state` check happens here, before
any token exchange.
"""

from __future__ import annotations

import pytest

from webex_nohello.scopes import REQUIRED_SCOPES
from webex_nohello.services.oauth import DEFAULT_PORT, interpret_callback, redirect_uri

EXPECTED = "the-expected-state"


def test_accepts_a_callback_carrying_a_code_and_the_matching_state() -> None:
    outcome = interpret_callback(
        {"code": ["auth-code"], "state": [EXPECTED]}, expected_state=EXPECTED
    )

    assert outcome.code == "auth-code"
    assert outcome.failure is None


@pytest.mark.parametrize(
    ("params", "because"),
    [
        ({"code": ["c"], "state": ["not-the-expected-state"]}, "state does not match"),
        ({"code": ["c"]}, "state is absent entirely"),
        ({"code": ["c"], "state": [""]}, "state is empty"),
    ],
)
def test_rejects_a_code_whose_state_does_not_match(
    params: dict[str, list[str]], because: str
) -> None:
    outcome = interpret_callback(params, expected_state=EXPECTED)

    assert outcome.code is None, because
    assert outcome.failure is not None
    assert "state parameter" in outcome.failure


def test_rejects_a_callback_with_no_code_at_all() -> None:
    outcome = interpret_callback({"state": [EXPECTED]}, expected_state=EXPECTED)

    assert outcome.code is None
    assert outcome.failure is not None
    assert "no authorization code" in outcome.failure


def test_state_is_checked_even_when_an_error_is_present() -> None:
    """An error response is reported without trusting anything else in the query."""
    outcome = interpret_callback(
        {"error": ["invalid_scope"], "state": ["wrong"]}, expected_state=EXPECTED
    )

    assert outcome.code is None
    assert outcome.failure is not None
    assert "invalid_scope" in outcome.failure


class TestRemediation:
    def test_invalid_scope_lists_every_required_scope(self) -> None:
        """The failure this actually hit in practice. "Try again" would be useless."""
        outcome = interpret_callback(
            {"error": ["invalid_scope"], "error_description": ["The requested scope is invalid."]},
            expected_state=EXPECTED,
        )

        assert outcome.remediation is not None
        for scope in REQUIRED_SCOPES:
            assert scope.name in outcome.remediation
        assert "developer.webex.com/my-apps" in outcome.remediation

    def test_access_denied_says_the_operator_declined(self) -> None:
        outcome = interpret_callback({"error": ["access_denied"]}, expected_state=EXPECTED)

        assert outcome.remediation is not None
        assert "declined" in outcome.remediation

    def test_redirect_uri_mismatch_points_at_the_mismatch(self) -> None:
        outcome = interpret_callback({"error": ["redirect_uri_mismatch"]}, expected_state=EXPECTED)

        assert outcome.remediation is not None
        assert "redirect URI" in outcome.remediation

    def test_an_unknown_error_still_carries_something_actionable(self) -> None:
        outcome = interpret_callback({"error": ["something_new"]}, expected_state=EXPECTED)

        assert outcome.remediation is not None
        assert "auth login" in outcome.remediation

    def test_an_error_with_no_description_does_not_render_as_none(self) -> None:
        outcome = interpret_callback({"error": ["invalid_request"]}, expected_state=EXPECTED)

        assert outcome.failure is not None
        assert "None" not in outcome.failure


def test_redirect_uri_is_built_from_the_port() -> None:
    assert redirect_uri(9123) == "http://localhost:9123/callback"
    assert redirect_uri(DEFAULT_PORT) == f"http://localhost:{DEFAULT_PORT}/callback"
