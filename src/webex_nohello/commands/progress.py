"""Drawing what a run is doing.

Both phases can sit quiet for seconds at a time — a scan makes one request per space, and
a classification takes a few seconds per candidate. Each names whoever is currently being
waited on, which is more useful than a percentage: if a run stalls, the last name shown is
where it stalled.

Bars are transient, so they erase themselves and leave the report unobstructed, and they
draw to stderr so stdout stays clean if the report is piped. Nothing is drawn at all unless
a terminal is watching: under launchd or cron a progress bar would fill the log with
control characters, so the check is on the stream rather than on a flag the operator has to
remember.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from webex_nohello.models.run.candidate import Candidate
from webex_nohello.models.webex.space import Space
from webex_nohello.services.scan import ScanProgress, SilentProgress

NAME_WIDTH = 28


class CandidateProgress(Protocol):
    def classifying(self, candidate: Candidate) -> None: ...


def _shorten(name: str) -> str:
    if len(name) > NAME_WIDTH:
        return name[: NAME_WIDTH - 1] + "…"
    return name


@contextmanager
def _display(*, total: int | None) -> Iterator[Progress]:
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn(
            "{task.completed} scanned" if total is None else "{task.completed}/{task.total}"
        ),
        TimeElapsedColumn(),
        console=Console(stderr=True),
        transient=True,
    ) as progress:
        yield progress


class RichScanProgress:
    """A spinner with a running count, not a percentage.

    The total is deliberately unknown: the space list is streamed and abandoned as soon as
    the cutoff is reached, so counting the spaces first would defeat the point. `total=None`
    renders as a pulsing bar, which is honest about that.
    """

    def __init__(self, progress: Progress) -> None:
        self._progress = progress
        self._task = progress.add_task("Listing your direct spaces", total=None)

    def listing_spaces(self) -> None:
        self._progress.update(self._task, description="Listing your direct spaces")

    def examining(self, space: Space) -> None:
        # Advanced before the request, so the name on screen is the space being waited on
        # rather than the one just finished.
        self._progress.advance(self._task)
        self._progress.update(self._task, description=f"Reading {self._name_of(space)}")

    def candidate_found(self, space: Space) -> None:
        self._progress.update(
            self._task, description=f"Reading {self._name_of(space)} — worth a look"
        )

    def stopped_at_cutoff(self) -> None:
        self._progress.update(self._task, description="Reached the last read position")

    def _name_of(self, space: Space) -> str:
        """A direct space is titled with the other person's name, which is the useful label."""
        return _shorten(space.title.strip() or space.id)


class RichCandidateProgress:
    def __init__(self, progress: Progress, total: int) -> None:
        self._progress = progress
        self._task = progress.add_task("Classifying", total=total)

    def classifying(self, candidate: Candidate) -> None:
        self._progress.advance(self._task)
        sender = candidate.sender_email or candidate.space.title or candidate.space.id
        self._progress.update(self._task, description=f"Asking about {_shorten(sender)}")


class SilentCandidateProgress:
    def classifying(self, candidate: Candidate) -> None:
        """Nothing to draw."""


@contextmanager
def scan_progress() -> Iterator[ScanProgress]:
    if not sys.stdout.isatty():
        yield SilentProgress()
        return

    with _display(total=None) as progress:
        yield RichScanProgress(progress)


@contextmanager
def classification_progress(total: int) -> Iterator[CandidateProgress]:
    if not sys.stdout.isatty():
        yield SilentCandidateProgress()
        return

    with _display(total=total) as progress:
        yield RichCandidateProgress(progress, total)
