"""Why a classified-as-replyable message was not replied to anyway.

Every rail in Article X gets a named reason rather than a silent skip. A run that classified
something as a bare greeting and then said nothing must be able to say which rail stopped it,
or the operator cannot tell a working safety net from a broken classifier.
"""

from __future__ import annotations

from enum import StrEnum


class WithheldReason(StrEnum):
    NOT_ADDRESSABLE = "not_addressable"
    IN_COOLDOWN = "in_cooldown"
    OVER_RUN_CAP = "over_run_cap"
    DRY_RUN = "dry_run"

    @property
    def description(self) -> str:
        return _DESCRIPTIONS[self]


_DESCRIPTIONS = {
    WithheldReason.NOT_ADDRESSABLE: "not on the allow list, or on the deny list",
    WithheldReason.IN_COOLDOWN: "already replied to recently",
    WithheldReason.OVER_RUN_CAP: "the per-run cap was reached",
    WithheldReason.DRY_RUN: "dry run; pass --commit to send",
}
