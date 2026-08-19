"""The `auth` command group: login, status, logout.

Holds no business rules (Article III.5) and no wiring. It collects input, calls
`AuthService`, and renders the result.
"""

from __future__ import annotations

import functools
import webbrowser
from pathlib import Path
from typing import Annotated

import typer
from pydantic import SecretStr

from webex_nohello import paths, ui
from webex_nohello.clock import system_clock
from webex_nohello.commands.registration_guide import print_registration_guide
from webex_nohello.models.auth.credential_report import CredentialReport
from webex_nohello.models.auth.credential_state import CredentialState
from webex_nohello.models.auth.oauth_app import OAuthApp
from webex_nohello.models.auth.refresh_outcome import RefreshOutcome
from webex_nohello.models.webex.person import Person
from webex_nohello.services import oauth
from webex_nohello.services.agent_cli import build_driver, installed_classifiers
from webex_nohello.services.auth import build_auth_service
from webex_nohello.services.config import write_starter_config

CALLBACK_TIMEOUT_SECONDS = 300.0
ORDERED_ALTERNATIVE = {"claude": "codex", "codex": "claude"}
EXIT_FAILURE = 1

app = typer.Typer(help="Sign in to Webex, and inspect or revoke that sign-in.")

_STATE_SUMMARY = {
    CredentialState.SIGNED_OUT: "Not signed in.",
    CredentialState.REFRESH_EXPIRED: "Signed in, but the refresh token has expired.",
    CredentialState.MISSING_SCOPES: "Signed in, but the grant is missing required scopes.",
    CredentialState.REJECTED: "Stored credentials look sound, but Webex rejected them.",
    CredentialState.READY: "Signed in, and Webex confirms the credentials work.",
}


@app.command()
def login(
    port: Annotated[
        int, typer.Option(help="Loopback port for the OAuth redirect.")
    ] = oauth.DEFAULT_PORT,
    client_id: Annotated[
        str | None, typer.Option(help="Integration client ID; prompted for if omitted.")
    ] = None,
    client_secret: Annotated[
        str | None, typer.Option(help="Integration client secret; prompted for if omitted.")
    ] = None,
    open_browser: Annotated[
        bool, typer.Option("--open-browser/--no-open-browser", help="Launch a browser.")
    ] = True,
) -> None:
    """Register a Webex integration and sign in with it."""
    service = build_auth_service()
    _confirm_replacing_existing_credentials(service.inspect())

    # Supplying either credential means the integration already exists, so the
    # walkthrough would just be noise. Each is still prompted for individually below.
    if client_id is None and client_secret is None:
        print_registration_guide(oauth.redirect_uri(port))
        typer.confirm("Have you created the integration?", default=False, abort=True)
        ui.blank()

    credentials = OAuthApp(
        client_id=client_id or typer.prompt("Client ID").strip(),
        client_secret=SecretStr(
            client_secret or typer.prompt("Client Secret", hide_input=True).strip()
        ),
    )
    ui.blank()

    outcome = service.login(
        credentials,
        port=port,
        announce=functools.partial(_announce, open_browser=open_browser),
        wait_for_code=_wait_for_code,
    )
    _report_login_success(outcome.person, port)


@app.command()
def status() -> None:
    """Check the stored Webex credentials, confirming with Webex that they work."""
    report = build_auth_service().verify()
    _report_status(report)
    # Non-zero so this is usable as a gate in a script, and by `schedule install`.
    if not report.is_ready:
        raise typer.Exit(EXIT_FAILURE)


@app.command()
def refresh() -> None:
    """Force a token refresh now, then confirm the new token works.

    Not needed in normal use: any command that talks to Webex refreshes itself when the
    access token nears expiry. This exists to exercise the refresh path on demand, and to
    extend the refresh token's own window when the tool has sat unused.
    """
    service = build_auth_service()
    outcome = service.refresh_now()
    _report_refresh(outcome)

    report = service.verify()
    ui.blank()
    _report_status(report)
    if not report.is_ready:
        raise typer.Exit(EXIT_FAILURE)


@app.command()
def logout() -> None:
    """Delete the stored Webex credentials from the OS keychain."""
    build_auth_service().logout()
    ui.success("Stored Webex credentials deleted.")
    ui.line("Revoke the integration itself at https://developer.webex.com/my-apps")


def _confirm_replacing_existing_credentials(current: CredentialReport) -> None:
    if current.state is CredentialState.SIGNED_OUT:
        return

    ui.warn(f"Already signed in as {current.person_display_name} <{current.person_email}>")
    typer.confirm("Replace those credentials?", default=False, abort=True)
    ui.blank()


def _announce(url: str, *, open_browser: bool) -> None:
    opened = webbrowser.open(url) if open_browser else False
    if opened:
        ui.line("Your browser should now be asking you to authorise the integration.")
        ui.line("If it did not open, visit this URL:")
    else:
        ui.line("Open this URL to authorise the integration:")
    ui.copyable(url)
    ui.blank()
    ui.line("Waiting for the callback...")


def _wait_for_code(port: int, expected_state: str) -> str:
    return oauth.wait_for_code(
        port=port, expected_state=expected_state, timeout_seconds=CALLBACK_TIMEOUT_SECONDS
    )


def _report_login_success(person: Person, port: int) -> None:
    ui.blank()
    ui.success(f"Signed in as {person.display_name} <{person.primary_email}>")
    ui.line(f"Credentials are stored in your OS keychain. Callback port: {port}.")

    # Written here rather than left for the operator to discover, because a signed-in
    # install with no config is a half-finished setup: `run` would work and reply to nobody,
    # with nothing on disk to explain why.
    config_path = paths.config_file()
    if write_starter_config(config_path):
        ui.blank()
        ui.success(f"Wrote a starter config to {config_path}")
        ui.indented(
            "Every setting is commented. Nothing will be replied to until you add an "
            "address to allow_list -- that is deliberate."
        )

    _report_classifier(config_path)

    ui.blank()
    ui.line("Next:")
    ui.bullet("webex-nohello auth status      confirm the sign-in")
    ui.bullet("webex-nohello doctor           check an unattended run would work")
    ui.bullet("webex-nohello run              see what it would do; sends nothing")
    ui.bullet("webex-nohello config reply     read the wording it would send, and where from")
    ui.bullet(f"edit {config_path}")


def _report_classifier(config_path: Path) -> None:
    """Say which CLI will judge messages, and that it is a choice.

    Worth saying here rather than leaving in the README: deciding whether a message is a bare
    greeting is the one part of this program that is not deterministic, so the operator should
    know which tool is making that call before it ever sends anything.
    """
    available = installed_classifiers()

    ui.blank()
    if not available:
        ui.warn("Neither 'claude' nor 'codex' is installed, so nothing can be classified yet.")
        ui.indented(
            "Install one and sign in. Claude Code: "
            "https://docs.claude.com/en/docs/claude-code -- Codex: "
            "https://developers.openai.com/codex/cli"
        )
        return

    chosen = build_driver(preference=available[0])
    ui.success(f"Messages will be judged by {chosen.name}.")

    if len(available) > 1:
        ui.indented(
            "Both claude and codex are installed. claude is preferred because it can be told "
            "directly to expose no tools, so the classifier has no route to Webex; codex needs "
            "an isolated home directory to achieve the same thing."
        )
        ui.indented('To switch, set classifier = "codex" in your config:')
        # Not via `indented`, which wraps prose and would break a long path mid-word.
        ui.line(f"       {config_path}")
    else:
        ui.indented(f"The other option is {ORDERED_ALTERNATIVE[available[0]]}, if you install it.")


def _report_refresh(outcome: RefreshOutcome) -> None:
    now = system_clock()
    ui.success("Refreshed.")
    ui.blank()
    ui.line("  Access token")
    ui.line(f"    was  {ui.format_expiry(outcome.previous.access_token_expires_at, now)}")
    ui.line(f"    now  {ui.format_expiry(outcome.current.access_token_expires_at, now)}")
    ui.line("  Refresh token")
    ui.line(f"    was  {ui.format_expiry(outcome.previous.refresh_token_expires_at, now)}")
    ui.line(f"    now  {ui.format_expiry(outcome.current.refresh_token_expires_at, now)}")

    if not outcome.is_refresh_token_rotated:
        ui.blank()
        ui.warn("Webex returned the same refresh token rather than rotating it.")


def _report_status(report: CredentialReport) -> None:
    now = system_clock()
    summary = _STATE_SUMMARY[report.state]

    if report.is_ready:
        ui.success(summary)
    elif report.state is CredentialState.SIGNED_OUT:
        ui.warn(summary)
        ui.line("Run 'webex-nohello auth login' to sign in.")
        return
    else:
        ui.failure(summary)

    ui.blank()
    ui.line(f"  Account        {report.person_display_name} <{report.person_email}>")
    ui.line(f"  Access token   {ui.format_expiry(report.access_token_expires_at, now)}")
    ui.line(f"  Refresh token  {ui.format_expiry(report.refresh_token_expires_at, now)}")

    if report.absent_scopes:
        ui.blank()
        ui.failure("Missing scopes:")
        for scope in report.absent_scopes:
            ui.bullet(scope)
        ui.line("Add them to the integration, then run 'webex-nohello auth login' again.")
    elif report.rejection is not None:
        ui.blank()
        ui.indented(report.rejection)
        ui.line("Run 'webex-nohello auth logout' then 'webex-nohello auth login'.")
    elif report.state is CredentialState.REFRESH_EXPIRED:
        ui.blank()
        ui.line("Run 'webex-nohello auth login' to sign in again.")
