"""How one preflight check turned out.

Three levels rather than a boolean, because "this will not work" and "this will work but
will send nothing" are different answers and only the first should stop a schedule being
armed. An unconfigured install is healthy; it is just quiet.
"""

from __future__ import annotations

from enum import StrEnum


class CheckOutcome(StrEnum):
    PASSED = "passed"
    # Worth saying, but not a reason to refuse: notably an empty allow_list.
    WARNED = "warned"
    FAILED = "failed"

    @property
    def is_fatal(self) -> bool:
        return self is CheckOutcome.FAILED
