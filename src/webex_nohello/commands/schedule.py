"""The `schedule` command group: run unattended.

`install` arms something that posts to colleagues with nobody watching, which makes it the
most consequential command here. So it runs the full preflight and refuses on any failure
(Article XIII.3), prints the exact artefact it is about to install (XIII.4), and asks.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

import typer

from webex_nohello import paths, ui
from webex_nohello.commands.doctor import probe_inference
from webex_nohello.models.config.settings import Settings
from webex_nohello.models.doctor.preflight_paths import PreflightPaths
from webex_nohello.models.schedule.schedule_plan import SchedulePlan
from webex_nohello.services.auth import build_auth_service
from webex_nohello.services.config import load_settings
from webex_nohello.services.doctor import DoctorService
from webex_nohello.services.schedule import build_scheduler, resolve_executable

DEFAULT_EVERY_MINUTES = 10
EXIT_FAILURE = 1

app = typer.Typer(help="Run unattended, on a timer.")


@app.command()
def install(
    every_minutes: Annotated[
        int, typer.Option("--every", help="Minutes between runs.")
    ] = DEFAULT_EVERY_MINUTES,
    show_only: Annotated[
        bool,
        typer.Option("--show-only", help="Print what would be installed, and install nothing."),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Install the schedule, after checking that an unattended run would actually work."""
    scheduler = build_scheduler()
    plan = SchedulePlan(
        executable=resolve_executable(),
        interval=timedelta(minutes=every_minutes),
        log_file=paths.run_log_file(),
    )

    ui.heading(f"About to schedule, via {scheduler.mechanism}")
    ui.blank()
    ui.line(f"  every        {every_minutes} minutes")
    ui.line(f"  command      {' '.join(plan.command)}")
    ui.line(f"  installs to  {scheduler.location}")
    ui.line(f"  output to    {plan.log_file}")
    ui.blank()
    ui.line("The artefact, in full:")
    ui.blank()
    for line_out in scheduler.render(plan).splitlines():
        ui.line(f"  │ {line_out}" if line_out else "  │")
    ui.blank()

    if show_only:
        ui.line("Nothing installed; --show-only was given.")
        return

    _report_who_gets_replies(load_settings(paths.config_file()))

    if not _preflight_passes():
        raise typer.Exit(EXIT_FAILURE)

    ui.blank()
    if not yes:
        typer.confirm(
            f"Arm this? It will send replies every {every_minutes} minutes, unattended",
            default=False,
            abort=True,
        )

    scheduler.install(plan)
    ui.blank()
    ui.success(f"Scheduled. First run within {every_minutes} minutes.")
    ui.blank()
    ui.line("To watch it, or stop it:")
    ui.bullet(f"tail -f {plan.log_file}")
    ui.bullet(f"touch {paths.paused_file()}        pause without touching the schedule")
    ui.bullet("webex-nohello schedule uninstall    remove it entirely")


@app.command()
def uninstall() -> None:
    """Remove the schedule. Safe to run when nothing is installed."""
    scheduler = build_scheduler()

    if scheduler.uninstall():
        ui.success(f"Removed the schedule from {scheduler.location}")
    else:
        ui.line("Nothing was installed; nothing to remove.")

    ui.blank()
    ui.line("Your credentials, config and reply history are untouched.")


@app.command()
def status() -> None:
    """Report whether a schedule is installed, and whether the system has loaded it."""
    scheduler = build_scheduler()
    state = scheduler.state()

    if not state.is_installed:
        ui.warn(f"No schedule installed ({scheduler.mechanism}).")
        ui.indented("Run 'webex-nohello schedule install' to add one.")
        return

    if state.is_loaded:
        ui.success(f"Scheduled via {scheduler.mechanism}: {state.description}")
    else:
        ui.failure(f"Installed but inert: {state.description}")
        ui.indented(f"The file is at {state.location} but the system is not running it.")

    ui.blank()
    ui.line(f"  schedule  {state.location}")
    ui.line(f"  output    {paths.run_log_file()}")

    if paths.paused_file().exists():
        ui.blank()
        ui.warn("Paused: the schedule will fire but every run will stop immediately.")
        ui.indented(f"Delete {paths.paused_file()} to resume.")


def _report_who_gets_replies(settings: Settings) -> None:
    """Said out loud before arming, because this is the part that surprises people."""
    if settings.opt_in_only and not settings.allow_list:
        ui.warn("As configured, nobody will be replied to: allow_list is empty.")
        ui.indented("The schedule will run and do nothing. That may be what you want for now.")
    elif settings.opt_in_only:
        ui.warn(f"Replies will go to {len(settings.allow_list)} named person(s) only.")
    else:
        ui.failure("Replies will go to ANYONE who sends you a bare greeting.")
        ui.indented(
            "opt_in_only is off, so this is not limited to people you have named. Every "
            "reply is sent from your own account and cannot be unsent."
        )


def _preflight_passes() -> bool:
    """Article XIII.3: refuse to arm something that cannot work.

    There is deliberately no flag to skip this. A schedule is exactly the situation where
    nobody is watching, so it is the worst possible place to allow "I know better".
    """
    ui.blank()
    ui.line("Checking that an unattended run would work...")
    report = DoctorService(
        build_auth_service(), PreflightPaths.real(), probe_inference=probe_inference
    ).examine()

    if report.is_healthy:
        ui.success("Preflight passed.")
        return True

    ui.blank()
    for check in report.failures:
        ui.failure(f"{check.name}: {check.detail}")
        if check.remediation:
            ui.indented(check.remediation)
    ui.blank()
    ui.failure("Not scheduling. Run 'webex-nohello doctor' for the full picture.")
    return False
