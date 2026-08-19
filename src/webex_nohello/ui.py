"""Terminal output helpers.

Everything the program prints goes through here, so `T20` can ban bare `print` and
output stays capturable in tests.
"""

from __future__ import annotations

import textwrap
from datetime import datetime

import typer

from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError

PROSE_WIDTH = 75


def blank() -> None:
    typer.echo("")


def line(text: str = "") -> None:
    typer.echo(text)


def heading(text: str) -> None:
    typer.secho(text, bold=True)


def success(text: str) -> None:
    typer.secho(f"✓ {text}", fg=typer.colors.GREEN)


def warn(text: str) -> None:
    typer.secho(f"! {text}", fg=typer.colors.YELLOW)


def failure(text: str) -> None:
    typer.secho(f"✗ {text}", fg=typer.colors.RED, err=True)


def copyable(value: str) -> None:
    typer.secho(f"    {value}", fg=typer.colors.CYAN, bold=True)


def labelled_copyable(label: str, value: str) -> None:
    """A form field name, then the value to paste into it on its own line.

    The value goes on its own line, unindented relative to the label, so that a
    terminal double-click or drag selects the value and nothing else.
    """
    typer.secho(f"     {label}:", dim=True)
    typer.secho(f"       {value}", fg=typer.colors.CYAN, bold=True)


def bullet(text: str) -> None:
    typer.echo(f"     - {text}")


def numbered(index: int, text: str) -> None:
    typer.secho(f"  {index}. {text}", bold=True)


def indented(text: str) -> None:
    """Wrapped explanatory prose. Never used for a value the operator must copy."""
    for line_out in textwrap.wrap(text, width=PROSE_WIDTH):
        typer.echo(f"     {line_out}")


def render_error(error: WebexNoHelloError) -> None:
    failure(error.message)
    if error.remediation:
        typer.secho(f"  {error.remediation}", fg=typer.colors.YELLOW, err=True)


def format_expiry(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return "unknown"
    days = (moment - now).days
    stamp = moment.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    if days < 0:
        return f"{stamp} (expired)"
    return f"{stamp} ({days} days from now)"
