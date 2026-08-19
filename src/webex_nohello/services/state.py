"""Persistence for high-water marks.

Writes are atomic (temp file, then rename) per Article X.8: a half-written state file
must not be able to make the next run re-examine messages it has already handled, which
in a committing run means replying twice.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError
from webex_nohello.models.run.scan_state import ScanState


class StateStore(Protocol):
    def load(self) -> ScanState: ...

    def save(self, state: ScanState) -> None: ...


class FileStateStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> ScanState:
        if not self._path.exists():
            return ScanState()

        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise WebexNoHelloError(
                f"Could not read the state file at {self._path}: {exc}",
                remediation="Check the file's permissions.",
            ) from exc

        try:
            return ScanState.model_validate_json(raw)
        except ValidationError as exc:
            # Never fall back to an empty state: that would read as "never run" and put
            # the entire message history back in scope (Article VI.6).
            raise WebexNoHelloError(
                f"The state file at {self._path} is not in a format this version "
                f"understands: {exc}",
                remediation=(
                    "This program will not guess, because treating it as empty would put "
                    "your whole message history back in scope. Inspect the file, and "
                    "delete it only if you accept that the next run re-reads everything "
                    "(it will not reply to any of it -- see the first-run behaviour)."
                ),
            ) from exc

    def save(self, state: ScanState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = state.model_dump_json(indent=2)

        # Created in the same directory, so the rename cannot cross a filesystem boundary
        # and therefore cannot degrade into a non-atomic copy.
        descriptor, name = tempfile.mkstemp(
            dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp"
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self._path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise WebexNoHelloError(
                f"Could not write the state file at {self._path}: {exc}",
                remediation="Check the directory exists and is writable.",
            ) from exc
