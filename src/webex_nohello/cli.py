"""Typer application assembly, and the one place errors become exit codes."""

from __future__ import annotations

import typer

from webex_nohello import ui
from webex_nohello.commands import auth, config, doctor, review, run, schedule
from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError

EXIT_FAILURE = 1

app = typer.Typer(
    help=(
        "Reply to content-free Webex greetings with a polite nudge towards nohello.net. "
        "Replies are sent from your own account."
    ),
    no_args_is_help=True,
)
app.add_typer(auth.app, name="auth")
app.add_typer(config.app, name="config")
app.add_typer(schedule.app, name="schedule")
app.command(name="run")(run.run)
app.command(name="review")(review.review)
app.command(name="doctor")(doctor.doctor)


def main() -> None:
    """Console entrypoint. Renders deliberate errors with their remediation, no traceback."""
    try:
        app()
    except WebexNoHelloError as error:
        ui.render_error(error)
        raise SystemExit(EXIT_FAILURE) from error
