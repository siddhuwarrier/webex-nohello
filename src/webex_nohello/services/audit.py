"""The append-only reply log, and the cooldowns derived from it.

One artefact serves both purposes deliberately. If cooldowns were tracked separately they
could disagree with the audit log, and the disagreement would be discovered by someone
receiving a second reply.

Every write is flushed and fsynced before returning, because the ordering guarantee that
makes Article X.7 work is "the record is durable before the message is sent". A buffered
write would let a crash lose the record and permit a duplicate on the next run.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from webex_nohello.models.audit.reply_record import ReplyEvent, ReplyRecord
from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError


class AuditLog(Protocol):
    def record(self, entry: ReplyRecord) -> None: ...

    def last_attempt_to(self, recipient_email: str) -> datetime | None: ...


class FileAuditLog:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def record(self, entry: ReplyRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = entry.model_dump_json() + "\n"
        try:
            # Opened per write, in append mode: concurrent appends of a single short line
            # do not interleave, and there is no long-lived handle to lose on a crash.
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise WebexNoHelloError(
                f"Could not write the audit log at {self._path}: {exc}",
                remediation=(
                    "This program will not send a reply it cannot record, because the record "
                    "is what stops it replying twice. Fix the permissions and try again."
                ),
            ) from exc

    def last_attempt_to(self, recipient_email: str) -> datetime | None:
        """When this person was last replied to, or None.

        Consults ATTEMPTED records only. An attempt that then failed still counts: the point
        of a cooldown is to bound how often someone is written to, and a failed send may
        still have arrived.
        """
        wanted = recipient_email.strip().lower()
        latest: datetime | None = None
        for entry in self._entries():
            if entry.event is not ReplyEvent.ATTEMPTED:
                continue
            if entry.recipient_email.strip().lower() != wanted:
                continue
            if latest is None or entry.at > latest:
                latest = entry.at
        return latest

    def _entries(self) -> Iterator[ReplyRecord]:
        if not self._path.exists():
            return
        try:
            content = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise WebexNoHelloError(
                f"Could not read the audit log at {self._path}: {exc}",
                remediation=(
                    "Cooldowns are derived from this file, so without it this program cannot "
                    "tell whether someone has already been replied to. Fix the permissions."
                ),
            ) from exc

        for number, line in enumerate(content.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                yield ReplyRecord.model_validate_json(line)
            except ValidationError as exc:
                # One unreadable line must not hide every cooldown in the file, but it must
                # not pass silently either.
                raise WebexNoHelloError(
                    f"Line {number} of the audit log at {self._path} is unreadable: {exc}",
                    remediation=(
                        "Repair or remove that line. Deleting the file resets every cooldown, "
                        "which means people may be replied to again sooner than intended."
                    ),
                ) from exc


def is_in_cooldown(last_attempt: datetime | None, now: datetime, cooldown: timedelta) -> bool:
    if last_attempt is None:
        return False
    return now - last_attempt < cooldown
