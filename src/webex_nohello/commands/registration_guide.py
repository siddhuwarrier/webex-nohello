"""The walkthrough shown before sign-in.

Webex will not hand a command-line tool credentials until the operator has registered
an integration of their own, so this is the first thing `auth login` prints. Every
value the operator has to type is offered ready to paste, and the scope list is derived
from `scopes.py` rather than restated, so the two cannot drift.
"""

from __future__ import annotations

from webex_nohello import ui
from webex_nohello.models.auth.copyable_value import CopyableValue
from webex_nohello.models.auth.registration_step import RegistrationStep
from webex_nohello.scopes import REQUIRED_SCOPES

PORTAL_URL = "https://developer.webex.com/my-apps/new/integration"

SUGGESTED_NAME = "<your-name>-webex-nohello"
SUGGESTED_DESCRIPTION = (
    "Personal automation for my own account. Reads my recent DMs and, "
    "when someone sends only a greeting with no question in it, replies in that thread "
    "asking for the actual request and linking to nohello.net."
)


def registration_steps(callback_url: str) -> tuple[RegistrationStep, ...]:
    return (
        RegistrationStep(
            title="Open the Webex integration form",
            detail="Sign in with the same Webex account whose messages you want read.",
            values_to_copy=(CopyableValue("URL", PORTAL_URL),),
        ),
        RegistrationStep(
            title="Fill in the Name and Description fields",
            detail=(
                "Replace <your-name> so the integration is identifiable in an org with "
                "several of these. If the form insists on an icon, any of the supplied "
                "defaults will do."
            ),
            values_to_copy=(
                CopyableValue("Name", SUGGESTED_NAME),
                CopyableValue("Description", SUGGESTED_DESCRIPTION),
            ),
        ),
        RegistrationStep(
            title="Set the redirect URI to exactly this",
            detail="It must match character for character, including the port and path.",
            values_to_copy=(CopyableValue("Redirect URI", callback_url),),
        ),
        RegistrationStep(
            title="Tick exactly these scopes, and no others",
            detail="Each is needed for a specific call; nothing broader is requested.",
            bullets=tuple(f"{scope.name} — {scope.reason}" for scope in REQUIRED_SCOPES),
        ),
        RegistrationStep(
            title="Add the integration, then copy the Client ID and Client Secret",
            detail=(
                "Webex shows the secret once. If you lose it, regenerate it on the "
                "integration's page rather than creating a second integration."
            ),
        ),
    )


def print_registration_guide(callback_url: str) -> None:
    ui.heading("Webex needs an integration of your own before this tool can sign in.")
    ui.line("It takes a couple of minutes and only has to be done once.")
    ui.blank()

    for index, step in enumerate(registration_steps(callback_url), start=1):
        ui.numbered(index, step.title)
        ui.indented(step.detail)
        for field in step.values_to_copy:
            ui.labelled_copyable(field.label, field.value)
        for item in step.bullets:
            ui.bullet(item)
        ui.blank()
