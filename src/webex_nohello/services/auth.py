"""The single place any command asks "am I signed in?", and the sign-in itself.

`inspect()` answers without side effects, for `auth status` and `doctor`.
`require()` returns a usable session or raises, refreshing proactively on the way.
Every command that touches Webex goes through `require()` and nothing else.

Side effects in `login()` are injected, so the flow is exercisable without a browser,
a loopback socket, or a network (Article III.8).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from pydantic import SecretStr

from webex_nohello.clock import Clock, system_clock
from webex_nohello.models.auth.authorization_request import AuthorizationRequest
from webex_nohello.models.auth.credential_report import CredentialReport
from webex_nohello.models.auth.credential_state import CredentialState
from webex_nohello.models.auth.login_outcome import LoginOutcome
from webex_nohello.models.auth.oauth_app import OAuthApp
from webex_nohello.models.auth.refresh_outcome import RefreshOutcome
from webex_nohello.models.auth.session import Session
from webex_nohello.models.auth.stored_credentials import StoredCredentials
from webex_nohello.models.auth.token_set import TokenSet
from webex_nohello.models.errors.enrolment_error import EnrolmentError
from webex_nohello.models.errors.not_authenticated_error import NotAuthenticatedError
from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError
from webex_nohello.models.webex.person import Person
from webex_nohello.scopes import missing_scopes
from webex_nohello.services.credentials import CredentialStore, KeyringCredentialStore
from webex_nohello.services.oauth import OAuthService, redirect_uri
from webex_nohello.services.webex import WebexService


class OAuthGateway(Protocol):
    """What this service needs of the OAuth layer. Declared here, per dependency inversion."""

    def authorization_request(self, app: OAuthApp, callback_url: str) -> AuthorizationRequest: ...

    def exchange_code(self, app: OAuthApp, *, code: str, callback_url: str) -> TokenSet: ...

    def refresh(self, app: OAuthApp, refresh_token: str) -> TokenSet: ...


class WebexGateway(Protocol):
    def get_me(self) -> Person: ...


type WebexServiceFactory = Callable[[SecretStr], WebexGateway]
type AuthorizationAnnouncer = Callable[[str], None]
type CodeWaiter = Callable[[int, str], str]


class AuthService:
    def __init__(
        self,
        *,
        store: CredentialStore,
        oauth: OAuthGateway,
        webex_factory: WebexServiceFactory,
        clock: Clock,
    ) -> None:
        self._store = store
        self._oauth = oauth
        self._webex_factory = webex_factory
        self._clock = clock

    def inspect(self) -> CredentialReport:
        """Describe the stored credentials without refreshing or writing anything."""
        credentials = self._store.load()
        if credentials is None:
            return CredentialReport(state=CredentialState.SIGNED_OUT)

        tokens = credentials.tokens
        absent = missing_scopes(tokens.granted_scopes)

        if not tokens.is_refresh_token_usable(self._clock()):
            state = CredentialState.REFRESH_EXPIRED
        elif absent:
            state = CredentialState.MISSING_SCOPES
        else:
            state = CredentialState.READY

        return CredentialReport(
            state=state,
            person_email=credentials.person_email,
            person_display_name=credentials.person_display_name,
            access_token_expires_at=tokens.access_token_expires_at,
            refresh_token_expires_at=tokens.refresh_token_expires_at,
            absent_scopes=absent if state is CredentialState.MISSING_SCOPES else (),
        )

    def verify(self) -> CredentialReport:
        """`inspect()`, then prove the token actually works by calling Webex.

        Local checks cannot tell a working grant from a corrupt one: a record with sound
        expiry timestamps and the right scopes still reads as READY when the stored token
        itself is unusable. Only Webex can settle it, so anything claiming readiness to
        the operator asks.

        This may refresh, and therefore write. That is credential maintenance rather than
        state mutation, and it is permitted here by Article XII.4.
        """
        report = self.inspect()
        if not report.is_ready:
            return report

        try:
            person = self._webex_factory(self.require().access_token).get_me()
        except WebexNoHelloError as exc:
            return replace(report, state=CredentialState.REJECTED, rejection=exc.message)

        # Prefer the live identity: a display name or address can change under us.
        return replace(
            report,
            person_email=person.primary_email,
            person_display_name=person.display_name,
        )

    def require(self) -> Session:
        """Return a usable session, refreshing the access token if it is near expiry."""
        _reject_unusable(self.inspect())

        credentials = self._store.load()
        if credentials is None:
            raise NotAuthenticatedError("credentials disappeared while being read")

        if not credentials.tokens.is_access_token_usable(self._clock()):
            credentials = self._refresh(credentials)

        return Session(
            access_token=credentials.tokens.access_token,
            person_email=credentials.person_email,
            person_display_name=credentials.person_display_name,
        )

    def refresh_now(self) -> RefreshOutcome:
        """Refresh regardless of expiry, and persist the result.

        `require()` refreshes only inside the leeway window, so this is the only way to
        exercise the refresh path on demand rather than waiting a fortnight for it. It
        also extends the refresh token's own window, which `require()` will not do while
        the access token is still healthy.
        """
        credentials = self._store.load()
        if credentials is None:
            raise NotAuthenticatedError("no credentials are stored for this user")
        if not credentials.tokens.is_refresh_token_usable(self._clock()):
            raise NotAuthenticatedError(
                "the refresh token expired at "
                f"{credentials.tokens.refresh_token_expires_at:%Y-%m-%d %H:%M %Z}"
            )

        previous = credentials.tokens
        return RefreshOutcome(previous=previous, current=self._refresh(credentials).tokens)

    def login(
        self,
        app: OAuthApp,
        *,
        port: int,
        announce: AuthorizationAnnouncer,
        wait_for_code: CodeWaiter,
    ) -> LoginOutcome:
        callback_url = redirect_uri(port)
        request = self._oauth.authorization_request(app, callback_url)

        announce(request.url)
        code = wait_for_code(port, request.state)

        tokens = self._oauth.exchange_code(app, code=code, callback_url=callback_url)

        absent = missing_scopes(tokens.granted_scopes)
        if absent:
            raise EnrolmentError(
                "The grant Webex issued is missing required scopes: " + ", ".join(absent),
                remediation=(
                    "Edit the integration in the developer portal, tick the missing "
                    "scopes, and run 'webex-nohello auth login' again."
                ),
            )

        person = self._webex_factory(tokens.access_token).get_me()
        _reject_non_human(person)

        credentials = StoredCredentials(
            app=app,
            tokens=tokens,
            person_email=person.primary_email,
            person_display_name=person.display_name,
        )
        self._store.save(credentials)
        return LoginOutcome(person=person, credentials=credentials)

    def logout(self) -> None:
        self._store.delete()

    def _refresh(self, credentials: StoredCredentials) -> StoredCredentials:
        tokens = self._oauth.refresh(
            credentials.app, credentials.tokens.refresh_token.get_secret_value()
        )
        refreshed = credentials.model_copy(update={"tokens": tokens})
        self._store.save(refreshed)
        return refreshed


def _reject_unusable(report: CredentialReport) -> None:
    if report.state is CredentialState.SIGNED_OUT:
        raise NotAuthenticatedError("no credentials are stored for this user")
    if report.state is CredentialState.REFRESH_EXPIRED:
        raise NotAuthenticatedError(
            f"the refresh token expired at {report.refresh_token_expires_at:%Y-%m-%d %H:%M %Z}"
        )
    if report.state is CredentialState.MISSING_SCOPES:
        raise NotAuthenticatedError(
            "the stored grant is missing required scopes: " + ", ".join(report.absent_scopes)
        )


def _reject_non_human(person: Person) -> None:
    """Catch the bot-token mistake at sign-in rather than at the first empty poll.

    Article VIII.1: a bot cannot see the operator's one-to-one spaces at all, and would
    post as itself. Authorising one yields a program that silently finds nothing, which
    is far harder to diagnose than a refusal here.
    """
    if person.is_human:
        return
    raise EnrolmentError(
        f"That grant belongs to a Webex account of type '{person.type}', not a person.",
        remediation=(
            "Sign in as yourself. A bot cannot read your one-to-one spaces, and its "
            "replies would come from the bot rather than from you."
        ),
    )


def build_auth_service() -> AuthService:
    """Wire the real service graph. Kept out of the command modules per Article III.5."""
    return AuthService(
        store=KeyringCredentialStore(),
        oauth=OAuthService(system_clock),
        webex_factory=WebexService,
        clock=system_clock,
    )
