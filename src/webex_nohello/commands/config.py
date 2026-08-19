"""The `config` command group: write a starter config, show the effective one, find the files.

`init` exists because the alternative is copying TOML out of the README, and a config that
governs whether messages get sent to colleagues is a bad thing to get subtly wrong by
transcription.
"""

from __future__ import annotations

from typing import Annotated

import typer

from webex_nohello import paths, ui
from webex_nohello.models.config.settings import Settings
from webex_nohello.services.config import load_settings, write_starter_config
from webex_nohello.services.reply_template import DEFAULT_TEMPLATE

app = typer.Typer(help="Write, inspect and locate the configuration.")

EXIT_FAILURE = 1


@app.command()
def init(
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing config file.")
    ] = False,
    with_template: Annotated[
        bool, typer.Option("--with-template", help="Also write a copy of the reply text to edit.")
    ] = False,
) -> None:
    """Write a starter config file, with every setting commented."""
    destination = paths.config_file()

    if not write_starter_config(destination, overwrite=force):
        ui.warn(f"{destination} already exists.")
        ui.indented("Pass --force to overwrite it, or edit it directly.")
        raise typer.Exit(EXIT_FAILURE)

    ui.success(f"Wrote {destination}")
    if with_template:
        _write_template(force=force)

    ui.blank()
    ui.line("Nothing will be replied to until you add an address to allow_list.")


@app.command()
def show() -> None:
    """Print the settings in force, and where they came from."""
    config_path = paths.config_file()
    settings = load_settings(config_path)
    template_path = paths.reply_template_file()

    if config_path.exists():
        ui.success(f"Using {config_path}")
    else:
        ui.warn(f"No config file at {config_path}; using defaults.")
        ui.indented("Run 'webex-nohello config init' to write one.")

    ui.blank()
    ui.line(f"  opt_in_only           {settings.opt_in_only}")
    ui.line(f"  allow_list            {_render_list(settings.allow_list)}")
    ui.line(f"  deny_list             {_render_list(settings.deny_list)}")
    ui.line(f"  cooldown_minutes      {settings.cooldown_minutes}")
    ui.line(f"  max_replies_per_run   {settings.max_replies_per_run}")
    ui.line(f"  confidence_threshold  {settings.confidence_threshold}")
    ui.line(f"  classifier            {settings.classifier}")
    ui.line(f"  classifier_model      {settings.classifier_model or '(each CLI default)'}")

    ui.blank()
    if template_path.exists():
        ui.success(f"Reply text from {template_path}")
    else:
        ui.line(f"Reply text: the built-in default. Edit a copy at {template_path}")

    _warn_if_nothing_can_be_sent(settings)


@app.command()
def path() -> None:
    """Print where every file this program owns lives."""
    for label, location in (
        ("config", paths.config_file()),
        ("reply text", paths.reply_template_file()),
        ("read marks", paths.scan_state_file()),
        ("audit log", paths.audit_log_file()),
        ("kill switch", paths.paused_file()),
    ):
        ui.line(f"  {label:<12} {location}")


@app.command()
def template(
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing reply template.")
    ] = False,
) -> None:
    """Write the default reply text to a file so it can be edited."""
    _write_template(force=force)


def _write_template(*, force: bool) -> None:
    destination = paths.reply_template_file()

    if destination.exists() and not force:
        ui.warn(f"{destination} already exists.")
        ui.indented("Pass --force to overwrite it, or edit it directly.")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(DEFAULT_TEMPLATE + "\n", encoding="utf-8")
    ui.success(f"Wrote {destination}")
    ui.indented(
        "Placeholders available: sender_first_name, sender_display_name, sender_email. "
        "Anything else is an error rather than rendering blank."
    )


def _warn_if_nothing_can_be_sent(settings: Settings) -> None:
    if settings.opt_in_only and not settings.allow_list:
        ui.blank()
        ui.warn("Nothing will be replied to: opt_in_only is on and allow_list is empty.")
        ui.indented("That is the default, and is deliberate. Add an address to change it.")


def _render_list(addresses: tuple[str, ...]) -> str:
    return ", ".join(addresses) if addresses else "(empty)"
