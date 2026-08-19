"""One preflight check and what it found."""

from __future__ import annotations

from dataclasses import dataclass

from webex_nohello.models.doctor.check_outcome import CheckOutcome


@dataclass(frozen=True)
class Check:
    name: str
    outcome: CheckOutcome
    detail: str
    # Article XII.2: a failure must say what to do, not merely that something is wrong.
    remediation: str | None = None

    @classmethod
    def passed(cls, name: str, detail: str) -> Check:
        return cls(name=name, outcome=CheckOutcome.PASSED, detail=detail)

    @classmethod
    def warned(cls, name: str, detail: str, remediation: str | None = None) -> Check:
        return cls(name=name, outcome=CheckOutcome.WARNED, detail=detail, remediation=remediation)

    @classmethod
    def failed(cls, name: str, detail: str, remediation: str) -> Check:
        return cls(name=name, outcome=CheckOutcome.FAILED, detail=detail, remediation=remediation)
