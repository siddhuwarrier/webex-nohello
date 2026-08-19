"""The `run` command: scan, classify, and reply.

A dry run unless `--commit` is given, and even then it sends nothing until the operator has
put an address in `allow_list`. The rails that decide what actually goes out live in
`services/dispatch.py`, not here.
"""

from __future__ import annotations

import shlex
from datetime import timedelta
from typing import Annotated

import typer

from webex_nohello import paths, ui
from webex_nohello.clock import system_clock
from webex_nohello.commands.progress import classification_progress, scan_progress
from webex_nohello.models.classify.assessment import Assessment
from webex_nohello.models.config.settings import Settings
from webex_nohello.models.reply.dispatch_outcome import DispatchResult, ReplyOutcome
from webex_nohello.models.reply.withheld_reason import WithheldReason
from webex_nohello.models.run.candidate import Candidate
from webex_nohello.models.run.scan_result import ScanResult
from webex_nohello.models.run.scan_state import ScanState
from webex_nohello.models.webex.person import Person
from webex_nohello.services.agent_cli import DEFAULT_MODEL, build_driver
from webex_nohello.services.audit import FileAuditLog
from webex_nohello.services.auth import build_auth_service
from webex_nohello.services.classify import ClassifierService
from webex_nohello.services.config import load_settings
from webex_nohello.services.dispatch import DispatchService
from webex_nohello.services.lock import is_paused, run_lock
from webex_nohello.services.reply_template import load_template
from webex_nohello.services.scan import (
    DEFAULT_CONTEXT_MESSAGES,
    DEFAULT_LOOKBACK,
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
    model: Annotated[str, typer.Option(help="Model for the classifier.")] = DEFAULT_MODEL,
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
            model=model,
            explain=explain,
            commit=commit,
        )


def _with_overrides(
    settings: Settings,
    *,
    confidence: float | None,
    cooldown_minutes: int | None,
    max_replies: int | None,
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
    model: str,
    explain: bool,
    commit: bool,
) -> None:
    operator, reader = _connect()
    store = FileStateStore(paths.scan_state_file())
    scanner = ScanService(reader, store, system_clock, context_messages=context_messages)
    lookback = timedelta(days=lookback_days) if lookback_days is not None else None

    ui.line(f"Signed in as {operator.display_name} <{operator.primary_email}>")
    _announce_window(store.load(), lookback)
    ui.blank()

    with scan_progress() as progress:
        result = scanner.scan(operator, max_spaces=max_spaces, lookback=lookback, progress=progress)
    _report(result, store_path=str(store.path))

    assessments: list[Assessment] = []
    if classify and result.candidates and not result.is_first_run:
        classifier = ClassifierService(
            build_driver(model=model),
            confidence_threshold=settings.confidence_threshold,
        )
        assessments = _classify_and_report(classifier, result.candidates, operator, explain=explain)
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
        _report_assessment(assessment)
        if explain:
            _explain(classifier, assessment.candidate, operator)

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
        _report_outcome(outcome)

    _preview_body(dispatcher, result)
    _report_cap(result, settings)
    _report_totals(result, commit=commit)


def _preview_body(dispatcher: DispatchService, result: DispatchResult) -> None:
    """Article X.2: a dry run must show exactly what it would post, not a summary of it."""
    would_send = [one for one in result.outcomes if one.withheld is WithheldReason.DRY_RUN]
    if not would_send:
        return

    ui.heading("The reply that would be posted, in full:")
    ui.blank()
    for line_out in dispatcher.preview(would_send[0].assessment).splitlines():
        ui.line(f"  │ {line_out}" if line_out else "  │")
    ui.blank()


def _report_cap(result: DispatchResult, settings: Settings) -> None:
    if not result.hit_run_cap:
        return

    ui.blank()
    ui.failure(
        f"More than {settings.max_replies_per_run} messages warranted a reply, so the rest "
        "were left alone."
    )
    ui.indented(
        "That is treated as a fault rather than a limit: wanting to send that many at once "
        "usually means the read positions are wrong, not that you had that many greetings. "
        "Check the list above before raising max_replies_per_run."
    )


def _report_totals(result: DispatchResult, *, commit: bool) -> None:
    ui.blank()
    if not commit:
        ui.warn("Dry run: nothing was sent. Pass --commit to send.")
    elif result.sent:
        ui.success(f"Sent {len(result.sent)} reply(ies).")
        ui.indented(f"Recorded in {paths.audit_log_file()}")
    else:
        ui.success("Nothing was sent.")

    if result.failed:
        ui.blank()
        ui.failure(f"{len(result.failed)} send(s) failed. They will not be retried.")
        ui.indented(
            "The attempt is already recorded, so this program will not try again: a message "
            "that may have arrived must not be sent twice."
        )


def _report_outcome(outcome: ReplyOutcome) -> None:
    candidate = outcome.assessment.candidate
    who = candidate.sender_email or candidate.space.title or "unknown sender"

    if outcome.was_sent:
        ui.success(f"sent to {who}")
    elif outcome.error is not None:
        ui.failure(f"failed to {who}: {outcome.error}")
    elif outcome.withheld is not None:
        ui.line(f"  held back for {who} — {outcome.withheld.description}")
    ui.copyable(candidate.message.excerpt())
    ui.blank()


def _report_assessment(assessment: Assessment) -> None:
    candidate = assessment.candidate
    sender = candidate.sender_email or candidate.space.title or "unknown sender"

    # The classifier's view only. Whether anything is actually sent is decided later, by the
    # rails in DispatchService, and reported under "Replies".
    if assessment.is_reply_warranted:
        ui.warn(f"{sender} — bare greeting")
    elif assessment.failure is not None:
        ui.warn(f"{sender} — could not classify")
    else:
        ui.success(f"{sender} — leaving alone")

    ui.copyable(candidate.message.excerpt())
    if assessment.was_prefiltered:
        ui.indented("too long to be a bare greeting; no inference needed")
    else:
        ui.indented(assessment.summary)
    ui.blank()


def _explain(classifier: ClassifierService, candidate: Candidate, operator: Person) -> None:
    """Article IX.10: a verdict must be reproducible by hand."""
    ui.line("     --- prompt ---")
    for line_out in classifier.prompt_for(candidate, operator).splitlines():
        ui.line(f"     {line_out}")
    ui.line("     --- command ---")
    ui.line(f"     {shlex.join(classifier.command_for(candidate, operator))}")
    ui.blank()


def _announce_window(state: ScanState, lookback: timedelta | None) -> None:
    """Say how far back this run will look, since that governs how long it takes."""
    if lookback is not None:
        ui.line(f"Reading direct spaces active in the last {lookback.days} days...")
    elif state.last_activity_seen is None:
        ui.line(
            f"First run: reading direct spaces active in the last {DEFAULT_LOOKBACK.days} days."
        )
        ui.indented(
            "Older spaces are not opened at all. A greeting older than that has already "
            "been ignored, and replying to it now would be stranger than staying quiet."
        )
    else:
        stamp = state.last_activity_seen.astimezone().strftime("%Y-%m-%d %H:%M")
        ui.line(f"Reading direct spaces active since {stamp}...")


def _connect() -> tuple[Person, WebexService]:
    """Authenticate and prove the token works, which is `run`'s preflight (Article XII.3)."""
    session = build_auth_service().require()
    reader = WebexService(session.access_token)
    return reader.get_me(), reader


def _report(result: ScanResult, *, store_path: str) -> None:
    ui.line(f"Examined {result.spaces_examined} direct spaces.")
    if result.stopped_at_cutoff:
        ui.indented("Stopped at the last read position; older spaces were never fetched.")
    if result.out_of_order_spaces:
        ui.blank()
        ui.warn(
            f"{result.out_of_order_spaces} spaces arrived out of order, which should not "
            "happen. Messages may have been skipped."
        )
        ui.indented(
            "The early stop assumes Webex returns spaces newest-first. Re-run with "
            "--lookback-days to cover the window properly, and please report this."
        )

    if result.is_first_run:
        ui.blank()
        ui.warn("First run: nothing would be replied to, whatever is found below.")
        ui.indented(
            "This program deliberately never replies on its first run, so it cannot work "
            "backwards through months of history. From the next run onwards, only messages "
            "newer than the positions recorded now are considered."
        )

    _report_candidates(result.candidates)
    _report_skipped(result)

    ui.blank()
    ui.line(f"Read positions: {store_path}")


def _report_candidates(candidates: tuple[Candidate, ...]) -> None:
    ui.blank()
    if not candidates:
        ui.success("No messages need looking at.")
        return

    ui.heading(f"{len(candidates)} message(s) would be examined by the classifier:")
    ui.blank()
    for candidate in candidates:
        _report_candidate(candidate)


def _report_candidate(candidate: Candidate) -> None:
    sender = candidate.message.person_email or candidate.space.title or "unknown sender"
    ui.line(f"  {sender}")
    ui.copyable(candidate.message.excerpt())

    when = candidate.message.created
    stamp = when.astimezone().strftime("%Y-%m-%d %H:%M") if when else "unknown time"
    context = (
        "only message in the space"
        if candidate.is_first_contact
        else f"{len(candidate.conversation)} messages of context"
    )
    ui.line(f"      {stamp}, {context}")
    ui.blank()


def _report_skipped(result: ScanResult) -> None:
    counts = result.counts_by_reason()
    if not counts:
        return

    ui.line(f"Skipped {len(result.skipped)} spaces:")
    for reason, count in sorted(counts.items(), key=lambda pair: -pair[1]):
        ui.line(f"    {count:>4}  {reason.description}")
