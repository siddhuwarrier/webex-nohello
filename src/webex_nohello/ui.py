"""Terminal output helpers.

Everything the program prints goes through here, so `T20` can ban bare `print`, output stays
capturable in tests, and — the reason this is a single funnel rather than scattered `echo`
calls — a scheduled run's output can be timestamped in one place.

**Timestamps appear only when stdout is not a terminal.** Interactively they are noise: the
operator is watching, and knows when they pressed return. In a log they are the difference
between a usable record and a wall of text, because a job polling every ten minutes appends
to the same file forever and nothing else says when anything happened.
"""

from __future__ import annotations

import sys
import textwrap
from datetime import datetime

import typer

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError

PROSE_WIDTH = 75
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S%z"


def _is_logging() -> bool:
    """Whether output is going somewhere nobody is watching in real time."""
    return not sys.stdout.isatty()


def _stamp() -> str:
    return datetime.now().astimezone().strftime(TIMESTAMP_FORMAT)


def _emit(text: str, *, err: bool = False, **style: object) -> None:
    """The single point every line leaves through, so the prefix cannot be forgotten."""
    if _is_logging():
        # A blank line stays blank: a lone timestamp is noise, and the blanks are what make
        # a long log skimmable. Colour is dropped too — escape codes in a file are worse
        # than no colour.
        typer.echo(f"{_stamp()}  {text}" if text else "", err=err)
        return
    typer.secho(text, err=err, **style)  # type: ignore[arg-type]  # secho's kwargs are untyped


def blank() -> None:
    _emit("")


def line(text: str = "") -> None:
    _emit(text)


def heading(text: str) -> None:
    _emit(text, bold=True)


def success(text: str) -> None:
    _emit(f"✓ {text}", fg=typer.colors.GREEN)


def warn(text: str) -> None:
    _emit(f"! {text}", fg=typer.colors.YELLOW)


def failure(text: str) -> None:
    _emit(f"✗ {text}", fg=typer.colors.RED, err=True)


def copyable(value: str) -> None:
    _emit(f"    {value}", fg=typer.colors.CYAN, bold=True)


def labelled_copyable(label: str, value: str) -> None:
    """A form field name, then the value to paste into it on its own line.

    The value goes on its own line, unindented relative to the label, so that a
    terminal double-click or drag selects the value and nothing else.
    """
    _emit(f"     {label}:", dim=True)
    _emit(f"       {value}", fg=typer.colors.CYAN, bold=True)


def bullet(text: str) -> None:
    _emit(f"     - {text}")


def numbered(index: int, text: str) -> None:
    _emit(f"  {index}. {text}", bold=True)


def indented(text: str) -> None:
    """Wrapped explanatory prose. Never used for a value the operator must copy."""
    for line_out in textwrap.wrap(text, width=PROSE_WIDTH):
        _emit(f"     {line_out}")


def run_separator(what: str) -> None:
    """Mark the start of a run in a log, and do nothing interactively.

    An append-only log of runs every ten minutes otherwise reads as one continuous stream,
    and working out where a given run began means counting backwards.
    """
    if not _is_logging():
        return
    typer.echo("")
    typer.echo(f"{'=' * 20} {what} at {_stamp()} {'=' * 20}")


def render_error(error: WebexNoHelloError) -> None:
    failure(error.message)
    if error.remediation:
        _emit(f"  {error.remediation}", fg=typer.colors.YELLOW, err=True)


def format_expiry(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return "unknown"
    days = (moment - now).days
    stamp = moment.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    if days < 0:
        return f"{stamp} (expired)"
    return f"{stamp} ({days} days from now)"
