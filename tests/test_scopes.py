"""The scope set, its encoding, and the claim that the README stays in step with it."""

from __future__ import annotations

from pathlib import Path

from webex_nohello.scopes import REQUIRED_SCOPES, missing_scopes, scope_parameter

README = Path(__file__).resolve().parent.parent / "README.md"


def test_nothing_is_missing_from_a_full_grant() -> None:
    assert missing_scopes(scope_parameter()) == ()


def test_order_does_not_matter() -> None:
    """Webex reorders the scope string it echoes back, so this is load-bearing."""
    reversed_grant = " ".join(reversed([scope.name for scope in REQUIRED_SCOPES]))

    assert missing_scopes(reversed_grant) == ()


def test_extra_scopes_are_tolerated() -> None:
    assert missing_scopes(f"{scope_parameter()} spark:memberships_read") == ()


def test_an_empty_grant_is_missing_everything() -> None:
    assert missing_scopes("") == tuple(scope.name for scope in REQUIRED_SCOPES)


def test_a_partial_grant_names_only_what_is_absent() -> None:
    assert missing_scopes("spark:people_read spark:rooms_read") == (
        "spark:messages_read",
        "spark:messages_write",
    )


def test_no_scope_is_a_prefix_of_another() -> None:
    """Membership is checked by exact token, so a prefix could not cause a false pass —
    but if that ever changed to substring matching this test would catch it."""
    names = [scope.name for scope in REQUIRED_SCOPES]

    for name in names:
        others = [other for other in names if other != name]
        assert not any(other.startswith(name) for other in others)


def test_the_scope_parameter_is_space_delimited() -> None:
    """RFC 6749. A `+` here is what produced a live invalid_scope rejection."""
    encoded = scope_parameter()

    assert "+" not in encoded
    assert encoded.count(" ") == len(REQUIRED_SCOPES) - 1


def test_every_scope_has_a_reason() -> None:
    """Article VIII.3: each scope must be justified to the operator."""
    for scope in REQUIRED_SCOPES:
        assert scope.reason.strip()
        assert scope.reason != scope.name


def test_nothing_broader_than_necessary_is_requested() -> None:
    names = {scope.name for scope in REQUIRED_SCOPES}

    assert "spark:all" not in names
    assert not any(name.startswith("spark-admin:") for name in names)


def test_readme_documents_every_scope_with_its_reason() -> None:
    """Article VIII.3 says the README derives from this module. It is hand-written, so
    this test is what actually keeps the two in step."""
    readme = README.read_text(encoding="utf-8")

    for scope in REQUIRED_SCOPES:
        assert scope.name in readme, f"{scope.name} is not documented in README.md"
        assert scope.reason in readme, f"the reason for {scope.name} differs in README.md"
