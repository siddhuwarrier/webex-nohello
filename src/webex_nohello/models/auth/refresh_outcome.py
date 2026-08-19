"""The result of a forced token refresh, holding both sides so the change is showable."""

from __future__ import annotations

from dataclasses import dataclass

from webex_nohello.models.auth.token_set import TokenSet


@dataclass(frozen=True)
class RefreshOutcome:
    previous: TokenSet
    current: TokenSet

    @property
    def is_refresh_token_rotated(self) -> bool:
        """Whether Webex issued a new refresh token, invalidating the old one.

        Webex is expected to rotate on every refresh. Worth surfacing, because if it
        ever stops, the 90-day window stops being extended by use.
        """
        return (
            self.previous.refresh_token.get_secret_value()
            != self.current.refresh_token.get_secret_value()
        )
