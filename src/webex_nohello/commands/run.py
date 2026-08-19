"""The `run` command: scan, classify, and reply.

A dry run unless `--commit` is given, and even then it sends nothing until the operator has
put an address in `allow_list`. The rails that decide what actually goes out live in
`services/dispatch.py`, not here.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

import typer

from webex_nohello import paths, ui
from webex_nohello.clock import system_clock
from webex_nohello.commands import run_report
from webex_nohello.commands.progress import classification_progress, scan_progress
from webex_nohello.models.classify.assessment import Assessment
from webex_nohello.models.config.settings import Settings
from webex_nohello.models.run.candidate import Candidate
from webex_nohello.models.run.scan_result import ScanResult
from webex_nohello.models.webex.person import Person
from webex_nohello.services.agent_cli import CHOICES, build_driver
from webex_nohello.services.audit import FileAuditLog
from webex_nohello.services.auth import build_auth_service
from webex_nohello.services.classify import ClassifierService
from webex_nohello.services.config import load_settings
from webex_nohello.services.dispatch import DispatchService
from webex_nohello.services.lock import is_paused, run_lock
from webex_nohello.services.reply_template import load_template
from webex_nohello.services.scan import (
    DEFAULT_CONTEXT_MESSAGES,
    ScanService,
)
from webex_nohello.services.state import FileStateStore
from webex_nohello.services.webex import WebexService

EXIT_PAUSED = 2


def run(
    max_spaces: Annotated[
        int | None,
        typer.Option(help="Only examine this many spaces, most recently active first."),
    ] = None,
    context_messages: Annotated[
        int, typer.Option(help="How many recent messages to read per candidate space.")
    ] = DEFAULT_CONTEXT_MESSAGES,
    lookback_days: Annotated[
        int | None,
        typer.Option(
            help=(
                "Re-examine spaces active in the last N days, ignoring the recorded "
                "position. Defaults to the recorded position, or 7 days on a first run."
            )
        ),
    ] = None,
    classify: Annotated[
        bool,
        typer.Option("--classify/--no-classify", help="Ask the classifier about each candidate."),
    ] = True,
    classifier: Annotated[
        str | None,
        typer.Option(help=f"Which CLI classifies: {', '.join(CHOICES)}. Overrides the config."),
    ] = None,
    model: Annotated[
        str | None, typer.Option(help="Model for the classifier. Overrides the config.")
    ] = None,
    confidence: Annotated[
        float | None,
        typer.Option(help="Override confidence_threshold from the config for this run."),
    ] = None,
    cooldown_minutes: Annotated[
        int | None,
        typer.Option(help="Override cooldown_minutes for this run. 0 replies every time."),
    ] = None,
    max_replies: Annotated[
        int | None,
        typer.Option(help="Override max_replies_per_run for this run."),
    ] = None,
    explain: Annotated[
        bool,
        typer.Option(
            "--explain",
            help="Print the exact prompt and command used, so a verdict can be reproduced.",
        ),
    ] = False,
    commit: Annotated[
        bool,
        typer.Option(
            "--commit",
            help="Actually send the replies. Without this, nothing is posted.",
        ),
    ] = False,
) -> None:
    """Look for content-free greetings in your direct messages, and reply to them.

    A dry run by default: without --commit nothing is posted and no read positions move.
    """
    settings = _with_overrides(
        load_settings(paths.config_file()),
        confidence=confidence,
        cooldown_minutes=cooldown_minutes,
        max_replies=max_replies,
        classifier=classifier,
        classifier_model=model,
    )
    template = load_template(paths.reply_template_file())

    if is_paused(paths.paused_file()):
        ui.warn("Paused. Nothing will be read or sent.")
        ui.indented(f"Remove {paths.paused_file()} to resume.")
        raise typer.Exit(EXIT_PAUSED)

    with run_lock(paths.lock_file()):
        _execute(
            settings=settings,
            template=template,
            max_spaces=max_spaces,
            context_messages=context_messages,
            lookback_days=lookback_days,
            classify=classify,
            explain=explain,
            commit=commit,
        )


def _with_overrides(
    settings: Settings,
    *,
    confidence: float | None,
    cooldown_minutes: int | None,
    max_replies: int | None,
    classifier: str | None,
    classifier_model: str | None,
) -> Settings:
    """Apply the per-run flags on top of the config file.

    Only values actually supplied override, which is why each flag defaults to None rather
    than to the setting's default. A flag defaulting to 0.8 would silently beat a
    `confidence_threshold = 0.9` in the config, and the operator would never be told.
    """
    supplied = {
        key: value
        for key, value in (
            ("confidence_threshold", confidence),
            ("cooldown_minutes", cooldown_minutes),
            ("max_replies_per_run", max_replies),
            ("classifier", classifier),
            ("classifier_model", classifier_model),
        )
        if value is not None
    }
    if not supplied:
        return settings

    # Revalidated rather than assigned, so a flag is bound by the same limits as the file.
    return Settings.model_validate(settings.model_dump() | supplied)


def _execute(
    *,
    settings: Settings,
    template: str,
    max_spaces: int | None,
    context_messages: int,
    lookback_days: int | None,
    classify: bool,
    explain: bool,
    commit: bool,
) -> None:
    operator, reader = _connect()
    store = FileStateStore(paths.scan_state_file())
    scanner = ScanService(reader, store, system_clock, context_messages=context_messages)
    lookback = timedelta(days=lookback_days) if lookback_days is not None else None

    ui.line(f"Signed in as {operator.display_name} <{operator.primary_email}>")
    run_report.announce_window(store.load(), lookback)
    ui.blank()

    with scan_progress() as progress:
        result = scanner.scan(operator, max_spaces=max_spaces, lookback=lookback, progress=progress)
    run_report.report(result, store_path=str(store.path))

    assessments: list[Assessment] = []
    if classify and result.candidates and not result.is_first_run:
        service = ClassifierService(
            build_driver(preference=settings.classifier, model=settings.classifier_model),
            confidence_threshold=settings.confidence_threshold,
        )
        assessments = _classify_and_report(service, result.candidates, operator, explain=explain)
    elif result.candidates and result.is_first_run:
        ui.blank()
        ui.warn("Skipping classification: a first run replies to nothing regardless.")

    if assessments:
        dispatcher = DispatchService(
            reader,
            FileAuditLog(paths.audit_log_file()),
            settings,
            system_clock,
            template=template,
        )
        _dispatch_and_report(dispatcher, assessments, settings, commit=commit)

    _advance_read_positions(store, result, commit=commit)


def _advance_read_positions(store: FileStateStore, result: ScanResult, *, commit: bool) -> None:
    """Article X.2: a dry run must not advance the marks, or it changes what it observes.

    A first run is the exception, and deliberately so: Article VI.4 wants the positions
    recorded even though nothing was sent, so a later committing run cannot work backwards
    through months of history.
    """
    if result.is_first_run:
        store.save(result.proposed_state)
        ui.blank()
        ui.success(f"Recorded read positions for {len(result.proposed_state.marks)} spaces.")
        return

    if not commit:
        ui.blank()
        ui.line("Read positions unchanged; a dry run leaves them alone.")
        return

    store.save(result.proposed_state)


def _classify_and_report(
    classifier: ClassifierService,
    candidates: tuple[Candidate, ...],
    operator: Person,
    *,
    explain: bool,
) -> list[Assessment]:
    ui.blank()
    ui.heading(f"Classifying {len(candidates)} message(s) with {classifier.driver_name}")
    ui.indented("Each call takes a few seconds.")
    ui.blank()

    assessments = []
    with classification_progress(len(candidates)) as progress:
        for candidate in candidates:
            progress.classifying(candidate)
            assessments.append(classifier.assess(candidate, operator))

    for assessment in assessments:
        run_report.report_assessment(assessment)
        if explain:
            run_report.explain(classifier, assessment.candidate, operator)

    return assessments


def _dispatch_and_report(
    dispatcher: DispatchService,
    assessments: list[Assessment],
    settings: Settings,
    *,
    commit: bool,
) -> None:
    warranted = [one for one in assessments if one.is_reply_warranted]
    if not warranted:
        ui.blank()
        ui.success("Nothing warrants a reply.")
        return

    result = dispatcher.dispatch(assessments, commit=commit)

    ui.blank()
    ui.heading("Replies")
    ui.blank()
    for outcome in result.outcomes:
        run_report.report_outcome(outcome)

    run_report.preview_body(dispatcher, result)
    run_report.report_cap(result, settings)
    run_report.report_totals(result, commit=commit)


def _connect() -> tuple[Person, WebexService]:
    """Authenticate and prove the token works, which is `run`'s preflight (Article XII.3)."""
    session = build_auth_service().require()
    reader = WebexService(session.access_token)
    return reader.get_me(), reader
