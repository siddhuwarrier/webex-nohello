"""The minimum scope set, with the reason for each.

Article VIII.3 requires every scope be justified to the operator. This module is the
single source of that justification: the login walkthrough and the README both derive
from it rather than restating it.
"""

from __future__ import annotations

from webex_nohello.models.auth.scope import Scope

REQUIRED_SCOPES: tuple[Scope, ...] = (
    Scope("spark:people_read", "Identify you, and tell real people apart from bots"),
    Scope("spark:rooms_read", "List your one-to-one spaces"),
    Scope("spark:messages_read", "Read the recent messages in those spaces"),
    Scope("spark:messages_write", "Post the reply, as a threaded reply only"),
)


def scope_parameter() -> str:
    """The space-delimited value for the OAuth `scope` parameter."""
    return " ".join(scope.name for scope in REQUIRED_SCOPES)


def missing_scopes(granted: str) -> tuple[str, ...]:
    """Which required scopes a granted scope string lacks; empty when sufficient.

    Webex may return a different set from the one requested if the operator edited the
    integration, so the result is checked rather than assumed.
    """
    held = set(granted.split())
    return tuple(scope.name for scope in REQUIRED_SCOPES if scope.name not in held)
