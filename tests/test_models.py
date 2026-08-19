"""Model behaviour: token expiry arithmetic, response parsing, and person classification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr, ValidationError

from conftest import NOW, make_tokens
from webex_nohello.models.auth.stored_credentials import StoredCredentials
from webex_nohello.models.auth.token_response import TokenResponse
from webex_nohello.models.auth.token_set import REFRESH_LEEWAY
from webex_nohello.models.webex.person import Person

WEBEX_ACCESS_TOKEN_SECONDS = 1_209_600  # 14 days, as observed live
WEBEX_REFRESH_TOKEN_SECONDS = 7_776_000  # 90 days, as observed live


class TestTokenExpiry:
    def test_a_fresh_token_is_usable(self) -> None:
        assert make_tokens().is_access_token_usable(NOW)

    def test_a_token_inside_the_leeway_window_is_not_usable(self) -> None:
        tokens = make_tokens(access_valid_for=REFRESH_LEEWAY - timedelta(minutes=1))

        assert not tokens.is_access_token_usable(NOW)

    def test_a_token_just_outside_the_leeway_window_is_usable(self) -> None:
        tokens = make_tokens(access_valid_for=REFRESH_LEEWAY + timedelta(minutes=1))

        assert tokens.is_access_token_usable(NOW)

    def test_an_expired_token_is_not_usable(self) -> None:
        assert not make_tokens(access_valid_for=timedelta(days=-1)).is_access_token_usable(NOW)

    def test_the_refresh_token_has_no_leeway(self) -> None:
        """There is nothing to pre-empt: once it is gone, only a fresh sign-in helps."""
        tokens = make_tokens(refresh_valid_for=timedelta(minutes=1))

        assert tokens.is_refresh_token_usable(NOW)
        assert not tokens.is_refresh_token_usable(NOW + timedelta(minutes=2))


class TestTokenResponse:
    def _response(self, **overrides: object) -> TokenResponse:
        payload: dict[str, object] = {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": WEBEX_ACCESS_TOKEN_SECONDS,
            "refresh_token_expires_in": WEBEX_REFRESH_TOKEN_SECONDS,
        }
        payload.update(overrides)
        return TokenResponse.model_validate(payload)

    def test_expiry_instants_are_computed_from_the_supplied_now(self) -> None:
        tokens = self._response().to_token_set(NOW, requested_scopes="a b")

        assert tokens.access_token_expires_at == NOW + timedelta(seconds=WEBEX_ACCESS_TOKEN_SECONDS)
        assert tokens.refresh_token_expires_at == NOW + timedelta(
            seconds=WEBEX_REFRESH_TOKEN_SECONDS
        )

    def test_an_echoed_scope_wins_over_the_requested_one(self) -> None:
        """Webex does echo `scope`, reordered, so this is the path taken in practice."""
        tokens = self._response(scope="spark:messages_write spark:messages_read").to_token_set(
            NOW, requested_scopes="spark:messages_read spark:messages_write"
        )

        assert tokens.granted_scopes == "spark:messages_write spark:messages_read"

    def test_an_absent_scope_falls_back_to_what_was_requested(self) -> None:
        tokens = self._response().to_token_set(NOW, requested_scopes="spark:rooms_read")

        assert tokens.granted_scopes == "spark:rooms_read"

    def test_unknown_fields_are_tolerated(self) -> None:
        assert self._response(token_type="Bearer", something_new=1) is not None

    @pytest.mark.parametrize("field", ["expires_in", "refresh_token_expires_in"])
    def test_a_non_positive_lifetime_is_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError):
            self._response(**{field: 0})

    def test_a_missing_refresh_token_is_rejected(self) -> None:
        """Without one, a scheduled job is dead in 14 days; better to fail at sign-in."""
        with pytest.raises(ValidationError):
            TokenResponse.model_validate(
                {"access_token": "at", "expires_in": 100, "refresh_token_expires_in": 100}
            )


class TestStoredCredentials:
    def test_secrets_do_not_appear_in_the_repr(self) -> None:
        """Article VII.7: a traceback or log line must not disclose the token."""
        credentials = StoredCredentials(
            app={"client_id": "cid", "client_secret": "the-client-secret"},
            tokens=make_tokens(access_token="the-access-token"),
            person_email="me@example.com",
            person_display_name="Me",
        )

        rendered = repr(credentials)

        assert "the-access-token" not in rendered
        assert "the-client-secret" not in rendered

    def test_an_unknown_field_is_rejected(self) -> None:
        """extra=forbid, so a format change fails loudly instead of dropping a token."""
        with pytest.raises(ValidationError):
            StoredCredentials.model_validate(
                {
                    "version": 1,
                    "app": {"client_id": "c", "client_secret": "s"},
                    "tokens": make_tokens().model_dump(),
                    "person_email": "me@example.com",
                    "person_display_name": "Me",
                    "unexpected": "value",
                }
            )

    def test_a_future_format_version_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StoredCredentials.model_validate(
                {
                    "version": 2,
                    "app": {"client_id": "c", "client_secret": "s"},
                    "tokens": make_tokens().model_dump(),
                    "person_email": "me@example.com",
                    "person_display_name": "Me",
                }
            )

    def test_a_round_trip_through_json_preserves_the_secrets(self) -> None:
        original = StoredCredentials(
            app={"client_id": "cid", "client_secret": "sec"},
            tokens=make_tokens(access_token="at"),
            person_email="me@example.com",
            person_display_name="Me",
        )

        restored = StoredCredentials.model_validate_json(original.model_dump_json())

        assert restored.tokens.access_token.get_secret_value() == "at"
        assert restored.app.client_secret.get_secret_value() == "sec"
        assert restored.tokens.access_token_expires_at == original.tokens.access_token_expires_at


class TestPerson:
    def test_a_person_is_human(self) -> None:
        assert Person.model_validate({"id": "1", "type": "person"}).is_human

    @pytest.mark.parametrize("person_type", ["bot", "appuser", "something_new", ""])
    def test_anything_else_is_not_human(self, person_type: str) -> None:
        """Article VII.2: fail closed on an unrecognised type."""
        assert not Person.model_validate({"id": "1", "type": person_type}).is_human

    def test_the_camel_case_display_name_is_read(self) -> None:
        person = Person.model_validate({"id": "1", "displayName": "Ada Lovelace"})

        assert person.display_name == "Ada Lovelace"

    def test_the_first_email_is_the_primary_one(self) -> None:
        person = Person.model_validate({"id": "1", "emails": ["a@x.com", "b@x.com"]})

        assert person.primary_email == "a@x.com"

    def test_an_account_with_no_email_yields_an_empty_string(self) -> None:
        assert Person.model_validate({"id": "1"}).primary_email == ""

    def test_a_type_absent_from_the_payload_defaults_to_human(self) -> None:
        """The five permitted calls all return `type`; this only guards a truncated body."""
        assert Person.model_validate({"id": "1"}).is_human


def test_naive_datetimes_are_not_silently_accepted_as_utc() -> None:
    """A naive expiry would make the leeway comparison raise at the worst moment."""
    naive = datetime(2026, 8, 19, 12, 0)  # noqa: DTZ001  # deliberately naive, that is the point
    aware = make_tokens()

    with pytest.raises(TypeError):
        _ = aware.access_token_expires_at < naive

    assert aware.access_token_expires_at.tzinfo is not None
    assert make_tokens().is_access_token_usable(datetime.now(UTC).replace(year=2020))


def test_secret_str_is_used_for_every_credential_field() -> None:
    tokens = make_tokens()

    assert isinstance(tokens.access_token, SecretStr)
    assert isinstance(tokens.refresh_token, SecretStr)
