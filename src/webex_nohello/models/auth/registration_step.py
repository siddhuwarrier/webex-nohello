"""One step of the Webex integration registration walkthrough."""

from __future__ import annotations

from dataclasses import dataclass

from webex_nohello.models.auth.copyable_value import CopyableValue


@dataclass(frozen=True)
class RegistrationStep:
    title: str
    detail: str
    values_to_copy: tuple[CopyableValue, ...] = ()
    bullets: tuple[str, ...] = ()
