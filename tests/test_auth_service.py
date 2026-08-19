"""AuthService: classifying stored credentials, refreshing, and signing in."""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import (
    FAKE_AUTH_URL,
    FAKE_STATE,
    NOW,
    FailingWebexService,
    FakeCredentialStore,
    FakeOAuthService,
    FakeWebexService,
    RecordingAnnouncer,
    RecordingCodeWaiter,
    clock_at,
    make_app,
    make_credentials,
    make_person,
    make_tokens,
    webex_factory,
)
from webex_nohello.models.auth.credential_state import CredentialState
from webex_nohello.models.errors.enrolment_error import EnrolmentError
from webex_nohello.models.errors.not_authenticated_error import NotAuthenticatedError
from webex_nohello.models.errors.webex_api_error import WebexApiError
from webex_nohello.models.webex.person import Person
from webex_nohello.services.auth import AuthService

PORT = 8090


def build_service(
    store: FakeCredentialStore,
    oauth: FakeOAuthService | None = None,
    webex: FakeWebexService | FailingWebexService | None = None,
) -> AuthService:
    return AuthService(
        store=store,
        oauth=oauth if oauth is not None else FakeOAuthService(),
        webex_factory=webex_factory(webex if webex is not None else FakeWebexService()),
        clock=clock_at(),
    )


class TestInspect:
    def test_reports_signed_out_when_nothing_is_stored(self) -> None:
        report = build_service(FakeCredentialStore()).inspect()

        assert report.state is CredentialState.SIGNED_OUT
        assert not report.is_ready
        assert report.person_email is None

    def test_reports_ready_for_a_healthy_grant(self) -> None:
        report = build_service(FakeCredentialStore(make_credentials())).inspect()

        assert report.state is CredentialState.READY
        assert report.is_ready
        assert report.person_email == "me@example.com"
        assert report.absent_scopes == ()

    def test_reports_refresh_expired_once_the_refresh_token_has_lapsed(self) -> None:
        tokens = make_tokens(refresh_valid_for=timedelta(days=-1))

        report = build_service(FakeCredentialStore(make_credentials(tokens))).inspect()

        assert report.state is CredentialState.REFRESH_EXPIRED
        assert not report.is_ready

    def test_reports_missing_scopes_and_names_them(self) -> None:
        tokens = make_tokens(granted_scopes="spark:people_read spark:rooms_read")

        report = build_service(FakeCredentialStore(make_credentials(tokens))).inspect()

        assert report.state is CredentialState.MISSING_SCOPES
        assert report.absent_scopes == ("spark:messages_read", "spark:messages_write")

    def test_an_expired_refresh_token_outranks_missing_scopes(self) -> None:
        """Both are fatal, but re-authenticating is the fix for both, so report the token."""
        tokens = make_tokens(
            refresh_valid_for=timedelta(days=-1), granted_scopes="spark:rooms_read"
        )

        report = build_service(FakeCredentialStore(make_credentials(tokens))).inspect()

        assert report.state is CredentialState.REFRESH_EXPIRED

    def test_writes_nothing(self) -> None:
        """Article XII.4: doctor and status must be safe to run at any time."""
        store = FakeCredentialStore(make_credentials(make_tokens(access_valid_for=timedelta(0))))

        build_service(store).inspect()

        assert store.writes == []
        assert store.deletes == 0


class TestVerify:
    def test_confirms_a_working_grant_against_webex(self) -> None:
        report = build_service(FakeCredentialStore(make_credentials())).verify()

        assert report.state is CredentialState.READY
        assert report.is_ready

    def test_reports_rejected_when_webex_refuses_the_token(self) -> None:
        """The masked-secret bug: sound-looking record, unusable token, READY reported."""
        webex = FailingWebexService(WebexApiError("Webex API returned HTTP 401: invalid token"))

        report = build_service(FakeCredentialStore(make_credentials()), webex=webex).verify()

        assert report.state is CredentialState.REJECTED
        assert not report.is_ready
        assert report.rejection is not None
        assert "401" in report.rejection

    def test_a_rejected_report_keeps_the_expiry_detail_for_display(self) -> None:
        webex = FailingWebexService(WebexApiError("nope"))
        store = FakeCredentialStore(make_credentials())

        report = build_service(store, webex=webex).verify()

        assert report.access_token_expires_at is not None
        assert report.refresh_token_expires_at is not None

    def test_prefers_the_live_identity_over_the_stored_one(self) -> None:
        """A display name can change after sign-in; the live value is the true one."""
        webex = FakeWebexService(
            Person.model_validate(
                {"id": "1", "emails": ["new@example.com"], "displayName": "New Name"}
            )
        )

        report = build_service(FakeCredentialStore(make_credentials()), webex=webex).verify()

        assert report.person_email == "new@example.com"
        assert report.person_display_name == "New Name"

    def test_does_not_call_webex_when_the_record_is_already_unusable(self) -> None:
        """No point asking Webex about a grant we know is missing scopes."""
        webex = FakeWebexService()
        store = FakeCredentialStore(
            make_credentials(make_tokens(granted_scopes="spark:rooms_read"))
        )

        report = build_service(store, webex=webex).verify()

        assert report.state is CredentialState.MISSING_SCOPES
        assert webex.tokens_used == []

    def test_does_not_call_webex_when_signed_out(self) -> None:
        webex = FakeWebexService()

        report = build_service(FakeCredentialStore(), webex=webex).verify()

        assert report.state is CredentialState.SIGNED_OUT
        assert webex.tokens_used == []

    def test_refreshes_a_near_expiry_token_before_verifying(self) -> None:
        """Permitted by Article XII.4: a refresh is credential maintenance, not state."""
        store = FakeCredentialStore(
            make_credentials(make_tokens(access_valid_for=timedelta(hours=6)))
        )
        oauth = FakeOAuthService()
        webex = FakeWebexService()

        report = build_service(store, oauth, webex).verify()

        assert report.state is CredentialState.READY
        assert oauth.refreshed_with == ["refresh-1"]
        assert webex.tokens_used == ["access-2"]


class TestRequire:
    def test_returns_a_session_without_refreshing_a_healthy_token(self) -> None:
        store = FakeCredentialStore(make_credentials())
        oauth = FakeOAuthService()

        session = build_service(store, oauth).require()

        assert session.access_token.get_secret_value() == "access-1"
        assert session.person_email == "me@example.com"
        assert oauth.refreshed_with == []
        assert store.writes == []

    def test_refreshes_inside_the_leeway_window_and_persists_the_result(self) -> None:
        """Article VIII.5: refresh proactively, so a scheduled run's first call cannot 401."""
        store = FakeCredentialStore(
            make_credentials(make_tokens(access_valid_for=timedelta(hours=6)))
        )
        oauth = FakeOAuthService()

        session = build_service(store, oauth).require()

        assert oauth.refreshed_with == ["refresh-1"]
        assert session.access_token.get_secret_value() == "access-2"
        assert len(store.writes) == 1
        assert store.writes[0].tokens.access_token.get_secret_value() == "access-2"

    def test_refresh_preserves_the_identity_already_established(self) -> None:
        store = FakeCredentialStore(
            make_credentials(make_tokens(access_valid_for=timedelta(hours=6)))
        )

        session = build_service(store, FakeOAuthService()).require()

        assert session.person_display_name == "Me"
        assert store.writes[0].person_email == "me@example.com"

    def test_raises_when_signed_out(self) -> None:
        with pytest.raises(NotAuthenticatedError) as caught:
            build_service(FakeCredentialStore()).require()

        assert "no credentials are stored" in caught.value.message
        assert caught.value.remediation is not None
        assert "auth login" in caught.value.remediation

    def test_raises_when_the_refresh_token_has_expired(self) -> None:
        store = FakeCredentialStore(
            make_credentials(make_tokens(refresh_valid_for=timedelta(days=-1)))
        )

        with pytest.raises(NotAuthenticatedError) as caught:
            build_service(store).require()

        assert "refresh token expired" in caught.value.message

    def test_raises_and_names_the_missing_scopes(self) -> None:
        store = FakeCredentialStore(
            make_credentials(make_tokens(granted_scopes="spark:rooms_read"))
        )

        with pytest.raises(NotAuthenticatedError) as caught:
            build_service(store).require()

        assert "spark:messages_write" in caught.value.message


class TestRefreshNow:
    def test_refreshes_even_when_the_access_token_is_perfectly_healthy(self) -> None:
        """This is the whole point: `require()` would decline to refresh here."""
        store = FakeCredentialStore(make_credentials())
        oauth = FakeOAuthService()

        outcome = build_service(store, oauth).refresh_now()

        assert oauth.refreshed_with == ["refresh-1"]
        assert outcome.previous.access_token.get_secret_value() == "access-1"
        assert outcome.current.access_token.get_secret_value() == "access-2"

    def test_persists_the_new_tokens(self) -> None:
        store = FakeCredentialStore(make_credentials())

        build_service(store, FakeOAuthService()).refresh_now()

        assert len(store.writes) == 1
        assert store.writes[0].tokens.access_token.get_secret_value() == "access-2"

    def test_keeps_the_stored_identity(self) -> None:
        store = FakeCredentialStore(make_credentials())

        build_service(store, FakeOAuthService()).refresh_now()

        assert store.writes[0].person_email == "me@example.com"

    def test_reports_rotation_when_the_refresh_token_changes(self) -> None:
        oauth = FakeOAuthService(
            refreshed=make_tokens(access_token="access-2", refresh_token="refresh-2")
        )

        outcome = build_service(FakeCredentialStore(make_credentials()), oauth).refresh_now()

        assert outcome.is_refresh_token_rotated

    def test_reports_no_rotation_when_webex_returns_the_same_refresh_token(self) -> None:
        """Would mean the 90-day window stops being extended by use, so it is surfaced."""
        oauth = FakeOAuthService(refreshed=make_tokens(access_token="access-2"))

        outcome = build_service(FakeCredentialStore(make_credentials()), oauth).refresh_now()

        assert not outcome.is_refresh_token_rotated

    def test_raises_when_signed_out(self) -> None:
        with pytest.raises(NotAuthenticatedError) as caught:
            build_service(FakeCredentialStore()).refresh_now()

        assert "no credentials are stored" in caught.value.message

    def test_refuses_when_the_refresh_token_has_already_expired(self) -> None:
        """Nothing to refresh with; sending it would just be a wasted round trip."""
        store = FakeCredentialStore(
            make_credentials(make_tokens(refresh_valid_for=timedelta(days=-1)))
        )
        oauth = FakeOAuthService()

        with pytest.raises(NotAuthenticatedError) as caught:
            build_service(store, oauth).refresh_now()

        assert "refresh token expired" in caught.value.message
        assert oauth.refreshed_with == []
        assert store.writes == []


class TestLogin:
    def test_stores_credentials_and_reports_the_person(self) -> None:
        store = FakeCredentialStore()
        waiter = RecordingCodeWaiter()
        announcer = RecordingAnnouncer()

        outcome = build_service(store).login(
            make_app(), port=PORT, announce=announcer, wait_for_code=waiter
        )

        assert outcome.person.primary_email == "me@example.com"
        assert store.credentials is not None
        assert store.credentials.person_display_name == "Me"
        assert announcer.urls == [FAKE_AUTH_URL]

    def test_passes_the_generated_state_to_the_callback_waiter(self) -> None:
        """The state check is only a defence if the value actually reaches the receiver."""
        waiter = RecordingCodeWaiter()

        build_service(FakeCredentialStore()).login(
            make_app(), port=PORT, announce=RecordingAnnouncer(), wait_for_code=waiter
        )

        assert waiter.seen_states == [FAKE_STATE]
        assert waiter.seen_ports == [PORT]

    def test_exchanges_exactly_the_code_the_callback_returned(self) -> None:
        oauth = FakeOAuthService()

        build_service(FakeCredentialStore(), oauth).login(
            make_app(),
            port=PORT,
            announce=RecordingAnnouncer(),
            wait_for_code=RecordingCodeWaiter(code="code-xyz"),
        )

        assert oauth.exchanged_codes == ["code-xyz"]

    def test_rejects_a_partial_grant_and_stores_nothing(self) -> None:
        store = FakeCredentialStore()
        oauth = FakeOAuthService(issued=make_tokens(granted_scopes="spark:rooms_read"))

        with pytest.raises(EnrolmentError) as caught:
            build_service(store, oauth).login(
                make_app(),
                port=PORT,
                announce=RecordingAnnouncer(),
                wait_for_code=RecordingCodeWaiter(),
            )

        assert "spark:messages_write" in caught.value.message
        assert store.credentials is None
        assert store.writes == []

    def test_rejects_a_bot_grant_and_stores_nothing(self) -> None:
        """Article VIII.1: a bot cannot see 1:1 spaces, so this must fail loudly at sign-in."""
        store = FakeCredentialStore()
        webex = FakeWebexService(make_person(person_type="bot"))

        with pytest.raises(EnrolmentError) as caught:
            build_service(store, webex=webex).login(
                make_app(),
                port=PORT,
                announce=RecordingAnnouncer(),
                wait_for_code=RecordingCodeWaiter(),
            )

        assert "not a person" in caught.value.message
        assert store.credentials is None

    def test_rejects_an_unrecognised_account_type(self) -> None:
        """Fails closed: an unknown future type must not be treated as a human."""
        webex = FakeWebexService(make_person(person_type="appuser"))

        with pytest.raises(EnrolmentError):
            build_service(FakeCredentialStore(), webex=webex).login(
                make_app(),
                port=PORT,
                announce=RecordingAnnouncer(),
                wait_for_code=RecordingCodeWaiter(),
            )

    def test_verifies_identity_using_the_freshly_issued_token(self) -> None:
        webex = FakeWebexService()

        build_service(FakeCredentialStore(), webex=webex).login(
            make_app(),
            port=PORT,
            announce=RecordingAnnouncer(),
            wait_for_code=RecordingCodeWaiter(),
        )

        assert webex.tokens_used == ["access-1"]


class TestLogout:
    def test_deletes_the_stored_record(self) -> None:
        store = FakeCredentialStore(make_credentials())

        build_service(store).logout()

        assert store.credentials is None
        assert store.deletes == 1

    def test_is_safe_when_already_signed_out(self) -> None:
        store = FakeCredentialStore()

        build_service(store).logout()

        assert store.deletes == 1


def test_token_expiry_maths_is_timezone_aware() -> None:
    """DTZ is linted for, but the leeway boundary deserves an explicit assertion."""
    tokens = make_tokens(access_valid_for=timedelta(hours=13))

    assert tokens.is_access_token_usable(NOW)
    assert not tokens.is_access_token_usable(NOW + timedelta(hours=2))
