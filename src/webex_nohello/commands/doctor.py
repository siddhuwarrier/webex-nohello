"""The `doctor` command: is this install fit to run unattended?

Exits non-zero on any failure, which is what makes it usable as a gate — both by
`schedule install` and by anything else that wants to check before trusting a timer.
"""

from __future__ import annotations

from typing import Annotated

import typer

from webex_nohello import paths, ui
from webex_nohello.models.doctor.check import Check
from webex_nohello.models.doctor.check_outcome import CheckOutcome
from webex_nohello.models.doctor.health_report import HealthReport
from webex_nohello.models.doctor.preflight_paths import PreflightPaths
from webex_nohello.services.agent_cli import build_driver
from webex_nohello.services.auth import build_auth_service
from webex_nohello.services.config import load_settings
from webex_nohello.services.doctor import DoctorService

EXIT_FAILURE = 1

PROBE_PROMPT = "Reply with the single word OK and nothing else."
PROBE_SYSTEM = "You are a connectivity check. Reply with one word."


def doctor(
    skip_inference: Annotated[
        bool,
        typer.Option(
            "--skip-inference",
            help="Do not call the classifier. Faster and free, but proves less.",
        ),
    ] = False,
) -> None:
    """Check everything an unattended run depends on, and say how to fix what is broken."""
    service = DoctorService(
        build_auth_service(),
        PreflightPaths.real(),
        probe_inference=None if skip_inference else probe_inference,
    )

    if not skip_inference:
        ui.line("Checking. The classifier call takes a few seconds.")
        ui.blank()

    report = service.examine()
    _render(report)

    if not report.is_healthy:
        raise typer.Exit(EXIT_FAILURE)


def probe_inference() -> str:
    """Prove the classifier is installed AND signed in, which only a real call can show.

    Uses the configured CLI rather than whichever happens to be installed, so `doctor` checks
    the one a run would actually use.
    """
    settings = load_settings(paths.config_file())
    driver = build_driver(preference=settings.classifier, model=settings.classifier_model)
    return f"{driver.name} answered: {driver.complete(PROBE_PROMPT, PROBE_SYSTEM)}"


def _render(report: HealthReport) -> None:
    for check in report.checks:
        _render_check(check)

    ui.blank()
    if report.is_healthy and not report.warnings:
        ui.success("Everything checks out.")
    elif report.is_healthy:
        ui.success(f"Fit to run, with {len(report.warnings)} thing(s) worth knowing above.")
    else:
        ui.failure(f"{len(report.failures)} check(s) failed. A scheduled run would not work.")


def _render_check(check: Check) -> None:
    label = f"{check.name:<20}"
    if check.outcome is CheckOutcome.PASSED:
        ui.success(f"{label} {check.detail}")
        return
    if check.outcome is CheckOutcome.WARNED:
        ui.warn(f"{label} {check.detail}")
    else:
        ui.failure(f"{label} {check.detail}")
    if check.remediation:
        ui.indented(check.remediation)
