"""The run lock of Article X.9.

Uses `flock` rather than a pid file with a staleness heuristic, because the kernel releases
a flock when the holding process dies. That makes a stale lock impossible by construction
rather than by guessing how long is too long — which matters on a laptop that sleeps, where
"the lock is an hour old" says nothing about whether the holder is alive.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError


class AlreadyRunningError(WebexNoHelloError):
    pass


@contextmanager
def run_lock(path: Path) -> Iterator[None]:
    """Hold the lock for the duration, or refuse to start.

    Overlapping runs are the obvious route to a double reply: two scans would each find the
    same candidate and each decide to answer it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunningError(
                f"Another run holds the lock at {path}.",
                remediation=(
                    "Wait for it to finish. If you are sure nothing is running, the lock is "
                    "released automatically when the holding process exits, so a lock that "
                    "persists means a process is still alive."
                ),
            ) from exc

        os.truncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode())
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def is_paused(path: Path) -> bool:
    """The kill switch: a file whose mere existence stops every run (Article X.6).

    A file rather than a config key on purpose. It works when the config is broken, it needs
    no parsing, and a scheduled run honours it without the schedule being touched.
    """
    return path.exists()
