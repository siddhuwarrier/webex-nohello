"""Every check, and the one question a scheduler needs answered."""

from __future__ import annotations

from dataclasses import dataclass, field

from webex_nohello.models.doctor.check import Check
from webex_nohello.models.doctor.check_outcome import CheckOutcome


@dataclass(frozen=True)
class HealthReport:
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def is_healthy(self) -> bool:
        """No failures. Warnings do not count: a quiet install still works."""
        return not any(check.outcome.is_fatal for check in self.checks)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.outcome is CheckOutcome.FAILED)

    @property
    def warnings(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.outcome is CheckOutcome.WARNED)
