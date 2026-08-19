"""Enforce Article IV.1: a module should be under 200 lines and must be under 300.

Article IV.2 matters more than this script: exceeding the soft limit is a prompt to
look, not an instruction to split. Fragmenting one concern to satisfy a line count is
the worse outcome.
"""

from __future__ import annotations

import sys
from pathlib import Path

SOFT_LIMIT = 200
HARD_LIMIT = 300


def main(paths: list[str]) -> int:
    failed = False
    for raw in paths:
        path = Path(raw)
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > HARD_LIMIT:
            sys.stderr.write(f"{path}: {lines} lines exceeds the hard limit of {HARD_LIMIT}\n")
            failed = True
        elif lines > SOFT_LIMIT:
            sys.stderr.write(f"{path}: {lines} lines exceeds the soft limit of {SOFT_LIMIT}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
