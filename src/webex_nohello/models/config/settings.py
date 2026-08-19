"""What the operator can configure, and the defaults when they have not.

Every default here is the timid one. This program posts under the operator's own name to
real colleagues and cannot unsend, so a setting the operator has not thought about should
lean towards saying nothing.

`extra="forbid"` so a typo in the config file is reported rather than silently ignored
(Article XI.5) — a misspelled `deny_list` that quietly did nothing would be a bad way to
discover the mistake.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Article X.3. Short on purpose. This is not "the point is made once, repeating it is
# nagging" — someone who keeps sending content-free greetings keeps earning the nudge. The
# window exists only so that a burst within one interaction ("hi" … "hello?" … "you there?")
# draws a single reply rather than three.
DEFAULT_COOLDOWN_MINUTES = 30

# Article X.5. Wanting to exceed this means something is wrong, so it stops rather than
# proceeding.
DEFAULT_MAX_REPLIES_PER_RUN = 5


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Article X.4. True by default on purpose: until the operator has named someone, this
    # program replies to nobody at all, however confident the classifier is.
    opt_in_only: bool = True
    # Email addresses that may receive a reply. Only consulted when opt_in_only is true.
    allow_list: tuple[str, ...] = ()
    # Email addresses that never receive one. Consulted always, and wins over allow_list.
    deny_list: tuple[str, ...] = ()

    cooldown_minutes: int = Field(default=DEFAULT_COOLDOWN_MINUTES, ge=0)
    max_replies_per_run: int = Field(default=DEFAULT_MAX_REPLIES_PER_RUN, ge=0)
    confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def _explain_renamed_keys(cls, data: object) -> object:
        """Name the replacement for a key that used to exist.

        `extra="forbid"` would otherwise report `cooldown_days` as merely unrecognised, which
        is true but unhelpful: the operator would not learn that the unit changed from days
        to minutes, and a silently dropped cooldown is a rail quietly disabled.
        """
        if isinstance(data, dict) and "cooldown_days" in data:
            raise ValueError(
                "cooldown_days was replaced by cooldown_minutes, and the unit changed. "
                "Someone who keeps sending greetings should keep hearing back, so the "
                "window is now short: cooldown_minutes = 30"
            )
        return data

    def is_addressable(self, email: str) -> bool:
        """Whether this person may receive a reply at all, before any other rail applies."""
        address = email.strip().lower()
        if not address:
            # No address means no way to honour a deny list, so refuse.
            return False
        if address in self._normalised(self.deny_list):
            return False
        if self.opt_in_only:
            return address in self._normalised(self.allow_list)
        return True

    @staticmethod
    def _normalised(addresses: tuple[str, ...]) -> set[str]:
        return {address.strip().lower() for address in addresses}
