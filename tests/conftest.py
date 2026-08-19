"""Builders and fakes. The suite runs offline: no SDK call, no socket, no keychain.

The fakes satisfy the protocols in `services.auth` structurally, so mypy checks them
against the real interfaces without either side importing the other.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr

from webex_nohello.models.audit.reply_record import ReplyRecord
from webex_nohello.models.auth.authorization_request import AuthorizationRequest
from webex_nohello.models.auth.oauth_app import OAuthApp
from webex_nohello.models.auth.stored_credentials import StoredCredentials
from webex_nohello.models.auth.token_set import TokenSet
from webex_nohello.models.classify.assessment import Assessment
from webex_nohello.models.classify.verdict import Verdict
from webex_nohello.models.classify.verdict_kind import VerdictKind
from webex_nohello.models.errors.webex_api_error import WebexApiError
from webex_nohello.models.run.candidate import Candidate
from webex_nohello.models.run.high_water_mark import HighWaterMark
from webex_nohello.models.run.scan_state import ScanState
from webex_nohello.models.webex.message import Message
from webex_nohello.models.webex.person import Person
from webex_nohello.models.webex.space import Space
from webex_nohello.scopes import scope_parameter
from webex_nohello.services.auth import WebexGateway
from webex_nohello.services.inference import InferenceError

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
FAKE_STATE = "state-abc123"
FAKE_AUTH_URL = "https://webexapis.com/v1/authorize?client_id=cid&state=state-abc123"


def clock_at(moment: datetime = NOW) -> Callable[[], datetime]:
    return lambda: moment


def make_app() -> OAuthApp:
    return OAuthApp(client_id="cid", client_secret=SecretStr("shh"))


def make_tokens(
    *,
    access_valid_for: timedelta = timedelta(days=14),
    refresh_valid_for: timedelta = timedelta(days=90),
    granted_scopes: str | None = None,
    access_token: str = "access-1",
    refresh_token: str = "refresh-1",
) -> TokenSet:
    return TokenSet(
        access_token=SecretStr(access_token),
        access_token_expires_at=NOW + access_valid_for,
        refresh_token=SecretStr(refresh_token),
        refresh_token_expires_at=NOW + refresh_valid_for,
        granted_scopes=scope_parameter() if granted_scopes is None else granted_scopes,
    )


def make_credentials(tokens: TokenSet | None = None) -> StoredCredentials:
    return StoredCredentials(
        app=make_app(),
        tokens=tokens if tokens is not None else make_tokens(),
        person_email="me@example.com",
        person_display_name="Me",
    )


def make_person(
    *,
    person_id: str = "person-1",
    email: str = "me@example.com",
    display_name: str = "Me",
    person_type: str = "person",
) -> Person:
    return Person.model_validate(
        {
            "id": person_id,
            "emails": [email],
            "displayName": display_name,
            "type": person_type,
        }
    )


def make_space(
    space_id: str,
    title: str = "",
    last_activity: datetime | None = NOW,
) -> Space:
    return Space.model_validate(
        {
            "id": space_id,
            "title": title,
            "type": "direct",
            "lastActivity": last_activity.isoformat() if last_activity is not None else None,
        }
    )


def make_message(
    message_id: str,
    *,
    sender: Person,
    text: str = "",
    created: datetime | None = None,
    parent_id: str | None = None,
) -> Message:
    return Message.model_validate(
        {
            "id": message_id,
            "roomId": "room-1",
            "personId": sender.id,
            "personEmail": sender.primary_email,
            "text": text,
            "created": (created or NOW).isoformat(),
            "parentId": parent_id,
        }
    )


def make_state(marks: dict[str, str], last_activity_seen: datetime | None = None) -> ScanState:
    return ScanState(
        marks={
            space_id: HighWaterMark(space_id=space_id, message_id=message_id, created=NOW)
            for space_id, message_id in marks.items()
        },
        last_activity_seen=last_activity_seen,
    )


class InMemoryStateStore:
    """Test double for `FileStateStore`, satisfying the `StateStore` protocol."""

    def __init__(self, state: ScanState | None = None) -> None:
        self._state = state if state is not None else ScanState()
        self.writes: list[ScanState] = []

    def load(self) -> ScanState:
        return self._state

    def save(self, state: ScanState) -> None:
        self._state = state
        self.writes.append(state)


def make_candidate(
    *,
    text: str,
    sender: Person,
    conversation: list[Message] | None = None,
    space_id: str = "s1",
    title: str = "Them",
) -> Candidate:
    latest = make_message("m-latest", sender=sender, text=text)
    messages = [*(conversation or []), latest]
    return Candidate(
        space=make_space(space_id, title),
        message=latest,
        conversation=tuple(messages),
    )


def make_assessment(
    *,
    sender: Person,
    text: str = "hi",
    warranted: bool = True,
    confidence: float = 0.95,
    reason: str = "bare greeting",
) -> Assessment:
    return Assessment(
        candidate=make_candidate(text=text, sender=sender),
        verdict=Verdict(verdict=VerdictKind.GREETING_ONLY, confidence=confidence, reason=reason),
        is_reply_warranted=warranted,
    )


class FakeWebexPoster:
    """Records what would be posted, satisfying the part of WebexService dispatch uses."""

    def __init__(
        self,
        *,
        fail_with: Exception | None = None,
        fail_on_first: Exception | None = None,
        timeline: list[str] | None = None,
    ) -> None:
        self.posted: list[tuple[str, str, str]] = []
        self._fail_with = fail_with
        self._fail_on_first = fail_on_first
        self._calls = 0
        # Shared with FakeAuditLog so a test can assert the record precedes the send.
        self._timeline = timeline

    def post_thread_reply(self, space_id: str, parent_id: str, markdown: str) -> Message:
        self._calls += 1
        if self._timeline is not None:
            self._timeline.append("post")
        if self._fail_with is not None:
            raise self._fail_with
        if self._fail_on_first is not None and self._calls == 1:
            raise self._fail_on_first

        self.posted.append((space_id, parent_id, markdown))
        return Message.model_validate({"id": f"posted-{self._calls}", "roomId": space_id})


class FakeAuditLog:
    """In-memory audit log, satisfying the `AuditLog` protocol."""

    def __init__(
        self,
        *,
        last_attempts: dict[str, datetime] | None = None,
        fail_on_record: bool = False,
        timeline: list[str] | None = None,
    ) -> None:
        self.records: list[ReplyRecord] = []
        self._last_attempts = {
            address.strip().lower(): moment for address, moment in (last_attempts or {}).items()
        }
        self._fail_on_record = fail_on_record
        self._timeline = timeline

    def record(self, entry: ReplyRecord) -> None:
        if self._fail_on_record:
            raise WebexApiError("the audit log could not be written")
        self.records.append(entry)
        if self._timeline is not None:
            self._timeline.append(f"audit:{entry.event.value}")

    def last_attempt_to(self, recipient_email: str) -> datetime | None:
        return self._last_attempts.get(recipient_email.strip().lower())


class FakeInferenceDriver:
    """Returns canned answers in order, satisfying the `InferenceDriver` protocol."""

    def __init__(self, answers: list[str], *, fail_with: str | None = None) -> None:
        self._answers = list(answers)
        self._fail_with = fail_with
        self.prompts: list[str] = []
        self.call_count = 0

    @property
    def name(self) -> str:
        return "fake-cli (test)"

    def command_for(self, prompt: str, system_prompt: str) -> list[str]:
        return ["fake-cli", "--print", prompt]

    def complete(self, prompt: str, system_prompt: str) -> str:
        self.call_count += 1
        self.prompts.append(prompt)
        if self._fail_with is not None:
            raise InferenceError(self._fail_with)
        if not self._answers:
            raise AssertionError("the driver was called more often than it had answers")
        return self._answers.pop(0)


class RecordingProgress:
    """Captures the progress callbacks in order, satisfying the `ScanProgress` protocol."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def listing_spaces(self) -> None:
        self.events.append("listing")

    def examining(self, space: Space) -> None:
        self.events.append(f"examining:{space.id}")

    def candidate_found(self, space: Space) -> None:
        self.events.append(f"found:{space.id}")

    def stopped_at_cutoff(self) -> None:
        self.events.append("stopped")


class FakeSpaceReader:
    """Stands in for Webex, recording the limits it was asked for.

    `get_person` raises for an unknown id, which is how an unresolvable author is
    simulated; the scan must fail closed rather than abandon the run.
    """

    def __init__(self, spaces: dict[Space, list[Message]], people: list[Person]) -> None:
        self._spaces = spaces
        self._people = {person.id: person for person in people}
        self.listed_spaces: list[str] = []
        self.message_limits: list[int] = []
        self.person_lookups: list[str] = []
        self.opened_spaces: list[str] = []

    def iter_direct_spaces(self) -> Iterator[Space]:
        """Yields lazily, so a test can assert the scan stopped fetching."""
        for space in self._spaces:
            self.listed_spaces.append(space.id)
            yield space

    def recent_messages(self, space_id: str, limit: int) -> tuple[Message, ...]:
        self.message_limits.append(limit)
        if space_id not in self.opened_spaces:
            self.opened_spaces.append(space_id)
        for space, messages in self._spaces.items():
            if space.id == space_id:
                return tuple(messages)
        return ()

    def get_person(self, person_id: str) -> Person:
        self.person_lookups.append(person_id)
        person = self._people.get(person_id)
        if person is None:
            raise WebexApiError(f"Webex refused to look up the person {person_id}: [404]")
        return person


class FakeCredentialStore:
    def __init__(self, credentials: StoredCredentials | None = None) -> None:
        self.credentials = credentials
        self.writes: list[StoredCredentials] = []
        self.deletes = 0

    def load(self) -> StoredCredentials | None:
        return self.credentials

    def save(self, credentials: StoredCredentials) -> None:
        self.credentials = credentials
        self.writes.append(credentials)

    def delete(self) -> None:
        self.credentials = None
        self.deletes += 1


class FakeOAuthService:
    def __init__(
        self, *, issued: TokenSet | None = None, refreshed: TokenSet | None = None
    ) -> None:
        self.issued = issued if issued is not None else make_tokens()
        self.refreshed = (
            refreshed if refreshed is not None else make_tokens(access_token="access-2")
        )
        self.exchanged_codes: list[str] = []
        self.refreshed_with: list[str] = []

    def authorization_request(self, app: OAuthApp, callback_url: str) -> AuthorizationRequest:
        return AuthorizationRequest(url=FAKE_AUTH_URL, state=FAKE_STATE)

    def exchange_code(self, app: OAuthApp, *, code: str, callback_url: str) -> TokenSet:
        self.exchanged_codes.append(code)
        return self.issued

    def refresh(self, app: OAuthApp, refresh_token: str) -> TokenSet:
        self.refreshed_with.append(refresh_token)
        return self.refreshed


class FakeWebexService:
    def __init__(self, person: Person | None = None) -> None:
        self.person = person if person is not None else make_person()
        self.tokens_used: list[str] = []

    def get_me(self) -> Person:
        return self.person


class FailingWebexService:
    """Stands in for a token Webex refuses, which local checks cannot detect."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.tokens_used: list[str] = []

    def get_me(self) -> Person:
        raise self.error


def webex_factory(
    fake: FakeWebexService | FailingWebexService,
) -> Callable[[SecretStr], WebexGateway]:
    """Adapt one fake into the factory shape `AuthService` expects."""

    def build(access_token: SecretStr) -> WebexGateway:
        fake.tokens_used.append(access_token.get_secret_value())
        return fake

    return build


class RecordingAnnouncer:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> None:
        self.urls.append(url)


class RecordingCodeWaiter:
    """Stands in for the loopback wait, capturing the state it was asked to expect."""

    def __init__(self, code: str = "auth-code-1") -> None:
        self.code = code
        self.seen_states: list[str] = []
        self.seen_ports: list[int] = []

    def __call__(self, port: int, expected_state: str) -> str:
        self.seen_ports.append(port)
        self.seen_states.append(expected_state)
        return self.code
