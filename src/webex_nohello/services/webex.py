"""Webex data access, over the official SDK.

The SDK owns the HTTP transport, pagination and rate-limit waiting. Its return values
are dynamic `ImmutableData` objects, so each is validated into a Pydantic model at this
boundary and nothing untyped escapes the module (Article V.1).
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import islice

from pydantic import BaseModel, SecretStr, ValidationError
from webexpythonsdk import WebexAPI
from webexpythonsdk.exceptions import ApiError

from webex_nohello.models.errors.webex_api_error import WebexApiError
from webex_nohello.models.webex.message import Message
from webex_nohello.models.webex.person import Person
from webex_nohello.models.webex.space import DIRECT_SPACE_TYPE, Space

# The SDK's check_type insists on an int here.
REQUEST_TIMEOUT_SECONDS = 15

UNAUTHORIZED = 401
FORBIDDEN = 403

# Page size for the space listing. Large enough that a normal run needs one request,
# small enough that the first page arrives quickly.
SPACE_PAGE_SIZE = 100


class WebexService:
    def __init__(self, access_token: SecretStr) -> None:
        self._api = WebexAPI(
            access_token=access_token.get_secret_value(),
            single_request_timeout=REQUEST_TIMEOUT_SECONDS,
            # Article VII.5: honour 429 rather than hammering. The SDK sleeps for the
            # Retry-After interval on our behalf.
            wait_on_rate_limit=True,
        )
        self._people: dict[str, Person] = {}

    def get_me(self) -> Person:
        try:
            payload = self._api.people.me().json_data
        except ApiError as exc:
            raise _translate(exc, "read your own profile") from exc
        return _validate(payload, Person, what="your own profile")

    def get_person(self, person_id: str) -> Person:
        """Resolve an author, so bots can be told from people (Article VII.2).

        Cached for the life of the service, per Article VII.3: a poll every few minutes
        would otherwise re-resolve the same handful of colleagues indefinitely.
        """
        cached = self._people.get(person_id)
        if cached is not None:
            return cached

        try:
            payload = self._api.people.get(person_id).json_data
        except ApiError as exc:
            raise _translate(exc, f"look up the person {person_id}") from exc

        person = _validate(payload, Person, what="a person")
        self._people[person_id] = person
        return person

    def iter_direct_spaces(self) -> Iterator[Space]:
        """One-to-one spaces, most recently active first, yielded page by page.

        **Lazy on purpose.** The SDK returns a generator that follows `Link: rel="next"`
        as it is consumed, so materialising it into a list fetches every page before any
        work is done — which for an account with thousands of spaces takes minutes and
        makes the Article VI.7 cutoff useless. Yielding lets the caller stop, which stops
        the pagination with it.

        Note that the SDK's `max` is the page size, not a total: it does not cap how many
        spaces come back. Capping is the caller's job.
        """
        try:
            for raw in self._api.rooms.list(
                type=DIRECT_SPACE_TYPE, sortBy="lastactivity", max=SPACE_PAGE_SIZE
            ):
                space = _validate(raw.json_data, Space, what="a space")
                # Belt and braces: the filter is server-side, but Article I.1 puts group
                # spaces permanently out of scope, so nothing downstream should trust that.
                if space.is_direct:
                    yield space
        except ApiError as exc:
            raise _translate(exc, "list your direct spaces") from exc

    def post_thread_reply(self, space_id: str, parent_id: str, markdown: str) -> Message:
        """Post a threaded reply. The only write this program is permitted to make.

        Deliberately not retried, per Article VII.5: a request that may have succeeded must
        never be sent twice, and the caller has already written its audit record on the
        assumption that this was attempted.
        """
        try:
            posted = self._api.messages.create(
                roomId=space_id, parentId=parent_id, markdown=markdown
            )
        except ApiError as exc:
            raise _translate(exc, f"post a reply in space {space_id}") from exc
        return _validate(posted.json_data, Message, what="the message just posted")

    def recent_messages(self, space_id: str, limit: int) -> tuple[Message, ...]:
        """The most recent messages in a space, newest first, as Webex returns them.

        `islice` is load-bearing, not decoration. The SDK's `max` sets the page size and
        its generator keeps following `Link: rel="next"` for as long as it is consumed, so
        draining it walks the space's entire history one page at a time. Taking exactly
        `limit` items stops after the first page, making this a single request.
        """
        try:
            listed = self._api.messages.list(roomId=space_id, max=limit)
            payloads = [message.json_data for message in islice(listed, limit)]
        except ApiError as exc:
            raise _translate(exc, f"read messages in space {space_id}") from exc

        return tuple(_validate(payload, Message, what="a message") for payload in payloads)


def _translate(exc: ApiError, action: str) -> WebexApiError:
    detail = f"Webex refused to {action}: {exc}"
    status = exc.status_code

    if status == UNAUTHORIZED:
        return WebexApiError(detail, remediation="Run 'webex-nohello auth login' to sign in again.")
    if status == FORBIDDEN:
        return WebexApiError(
            detail,
            remediation=(
                "The grant is probably missing a scope. Run 'webex-nohello auth login' "
                "and confirm every scope is ticked."
            ),
        )
    return WebexApiError(detail)


def _validate[T: BaseModel](payload: object, model: type[T], *, what: str) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise WebexApiError(
            f"Could not understand the Webex response describing {what}: {exc}",
            remediation="Report this with the Webex response shape.",
        ) from exc
