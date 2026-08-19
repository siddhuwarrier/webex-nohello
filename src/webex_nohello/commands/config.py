"""The `config` command group: write a starter config, show the effective one, find the files.

`init` exists because the alternative is copying TOML out of the README, and a config that
governs whether messages get sent to colleagues is a bad thing to get subtly wrong by
transcription. `reply` exists for the same reason in reverse: the text is long prose in a
file the config can point anywhere, so the only trustworthy way to check what would be sent —
and which file to edit to change it — is to be told both.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from webex_nohello import paths, ui
from webex_nohello.models.config.settings import Settings
from webex_nohello.models.reply.reply_placeholder import ReplyPlaceholder
from webex_nohello.models.webex.person import Person
from webex_nohello.services.config import load_settings, write_starter_config
from webex_nohello.services.reply_template import DEFAULT_TEMPLATE, load_reply, locate, render

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
    ui.line(f"  reply_file            {_reply_file_line(settings)}")

    _warn_if_nothing_can_be_sent(settings)


@app.command()
def path() -> None:
    """Print where every file this program owns lives."""
    settings = load_settings(paths.config_file())

    for label, location in (
        ("config", paths.config_file()),
        ("reply text", _reply_path(settings)),
        ("read marks", paths.scan_state_file()),
        ("audit log", paths.audit_log_file()),
        ("kill switch", paths.paused_file()),
    ):
        ui.line(f"  {label:<12} {location}")


@app.command()
def reply() -> None:
    """Print the reply that would be sent, and name the file it comes from."""
    source = load_reply(
        load_settings(paths.config_file()).reply_file, default_path=paths.reply_template_file()
    )

    if source.is_customised:
        ui.success(f"Reply text from {source.path}")
    else:
        ui.line("Reply text: the built-in default.")
        ui.indented(f"Write {source.path} to replace it, or 'config template' to start from this.")

    ui.blank()
    ui.line(render(source.text, _EXAMPLE_SENDER))
    ui.blank()
    ui.indented(
        f"Shown as {_EXAMPLE_SENDER.display_name} would receive it. "
        f"Placeholders: {', '.join(ReplyPlaceholder.names())}"
    )


@app.command()
def template(
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing reply template.")
    ] = False,
) -> None:
    """Write the default reply text to the file the config points at, so it can be edited."""
    _write_template(force=force)


def _write_template(*, force: bool) -> None:
    destination = _reply_path(load_settings(paths.config_file()))

    if destination.exists() and not force:
        ui.warn(f"{destination} already exists.")
        ui.indented("Pass --force to overwrite it, or edit it directly.")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(DEFAULT_TEMPLATE + "\n", encoding="utf-8")
    ui.success(f"Wrote {destination}")
    ui.indented(
        f"Placeholders available: {', '.join(ReplyPlaceholder.names())}. "
        "Anything else is an error rather than rendering blank."
    )


def _reply_path(settings: Settings) -> Path:
    return locate(settings.reply_file, default_path=paths.reply_template_file())


def _reply_file_line(settings: Settings) -> str:
    """Always the resolved path: a relative `reply_file` is the easiest thing here to misread."""
    resolved = _reply_path(settings)
    return f"{resolved}" if settings.reply_file else f"{resolved} (default location)"


def _warn_if_nothing_can_be_sent(settings: Settings) -> None:
    if settings.opt_in_only and not settings.allow_list:
        ui.blank()
        ui.warn("Nothing will be replied to: opt_in_only is on and allow_list is empty.")
        ui.indented("That is the default, and is deliberate. Add an address to change it.")


def _render_list(addresses: tuple[str, ...]) -> str:
    return ", ".join(addresses) if addresses else "(empty)"


_EXAMPLE_SENDER = Person(
    id="example", emails=["colleague@example.com"], display_name="Example Colleague"
)
