"""The outcome of one scan across every direct space."""

from __future__ import annotations

from dataclasses import dataclass, field

from webex_nohello.models.run.candidate import Candidate
from webex_nohello.models.run.scan_state import ScanState
from webex_nohello.models.run.skip_reason import SkipReason
from webex_nohello.models.webex.space import Space


@dataclass(frozen=True)
class SkippedSpace:
    space: Space
    reason: SkipReason


@dataclass(frozen=True)
class ScanResult:
    candidates: tuple[Candidate, ...]
    skipped: tuple[SkippedSpace, ...]
    # The marks as they would stand after this scan. Deliberately not persisted by the
    # scan itself: Article X.2 forbids a dry run from advancing them, so the decision to
    # write belongs to the caller.
    proposed_state: ScanState
    is_first_run: bool
    spaces_examined: int = field(default=0)
    # Whether the scan stopped early because the remaining spaces predate the recorded
    # position. When true, no further pages of the space list were fetched at all.
    stopped_at_cutoff: bool = field(default=False)
    # Spaces that arrived newer than the one before them, contradicting the descending
    # order the early stop relies on. Should always be zero; if it is not, the ordering
    # assumption in Article VI.7 is wrong and messages may be being skipped.
    out_of_order_spaces: int = field(default=0)

    def counts_by_reason(self) -> dict[SkipReason, int]:
        counts: dict[SkipReason, int] = {}
        for skip in self.skipped:
            counts[skip.reason] = counts.get(skip.reason, 0) + 1
        return counts
