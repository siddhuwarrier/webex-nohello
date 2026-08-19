"""The `schedule` command group: run unattended.

`install` arms something that posts to colleagues with nobody watching, which makes it the
most consequential command here. So it runs the full preflight and refuses on any failure
(Article XIII.4), prints the exact artefact it is about to install (XIII.5), runs it
once so a scheduled-only failure surfaces now rather than later (XIII.3), and asks.
"""

from __future__ import annotations

import os
import subprocess
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
from webex_nohello.services.schedule import (
    build_scheduler,
    resolve_executable,
    resolve_path_environment,
)

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
    first_run: Annotated[
        bool,
        typer.Option(
            "--first-run/--no-first-run",
            help="Run once immediately after installing, so you see the result now.",
        ),
    ] = True,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Install the schedule, after checking that an unattended run would actually work."""
    scheduler = build_scheduler()
    plan = SchedulePlan(
        executable=resolve_executable(),
        interval=timedelta(minutes=every_minutes),
        log_file=paths.run_log_file(),
        path_environment=resolve_path_environment(),
    )

    ui.heading(f"About to schedule, via {scheduler.mechanism}")
    ui.blank()
    ui.line(f"  every        {every_minutes} minutes")
    ui.line(f"  command      {' '.join(plan.command)}")
    ui.line(f"  installs to  {scheduler.location}")
    ui.line(f"  output to    {plan.log_file}")
    ui.line(f"  PATH         {plan.path_environment}")
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

    # Created now, empty, so the `tail -f` below works straight away. Otherwise the file
    # does not exist until the first run fires and the operator gets an error instead.
    plan.log_file.parent.mkdir(parents=True, exist_ok=True)
    plan.log_file.touch(exist_ok=True)

    scheduler.install(plan)
    ui.blank()
    ui.success(f"Scheduled: every {every_minutes} minutes.")

    if first_run:
        _run_once_now(plan)
    else:
        ui.line(f"First scheduled run within {every_minutes} minutes.")

    ui.blank()
    ui.line("To watch it, or stop it:")
    ui.bullet(f"tail -f {plan.log_file}")
    ui.bullet(f"touch {paths.paused_file()}        pause without touching the schedule")
    ui.bullet("webex-nohello schedule uninstall    remove it entirely")


def _run_once_now(plan: SchedulePlan) -> None:
    """Run the scheduled command once, in the foreground, with its output on screen.

    Done here rather than with launchd's RunAtLoad because that would put the output in the
    log where nobody is looking, and cron has no equivalent at all. Running it as a
    subprocess also exercises exactly what the schedule will do, including the pinned PATH —
    which is how a scheduled-only failure would be caught at install time rather than ten
    minutes later.
    """
    ui.blank()
    ui.heading("Running once now, as the schedule will:")
    ui.blank()

    environment = {**os.environ, "PATH": plan.path_environment}
    completed = subprocess.run(  # noqa: S603  # the command is the plan we just rendered
        plan.command, env=environment, stdin=subprocess.DEVNULL, check=False
    )

    ui.blank()
    if completed.returncode == 0:
        ui.success("That run succeeded, so the schedule should behave the same way.")
    else:
        ui.failure(f"That run exited {completed.returncode}. The schedule is installed anyway.")
        ui.indented(
            "Fix whatever it reported above, or remove the schedule with "
            "'webex-nohello schedule uninstall'. It will keep retrying every "
            f"{plan.interval_minutes} minutes until you do."
        )


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
    """Article XIII.4: refuse to arm something that cannot work.

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
