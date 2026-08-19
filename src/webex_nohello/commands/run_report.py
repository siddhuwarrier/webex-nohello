"""Everything the `run` command prints.

Split out because `run` was doing two jobs in one module: orchestrating a scan, a
classification and a dispatch, and rendering all three. The orchestration is what an
engineer debugging a missed reply needs to follow; the rendering was burying it.

Nothing here decides anything. Every function takes a finished result and prints it.
"""

from __future__ import annotations

import shlex
from datetime import timedelta

from webex_nohello import paths, ui
from webex_nohello.models.classify.assessment import Assessment
from webex_nohello.models.config.settings import Settings
from webex_nohello.models.reply.dispatch_outcome import DispatchResult, ReplyOutcome
from webex_nohello.models.reply.withheld_reason import WithheldReason
from webex_nohello.models.run.candidate import Candidate
from webex_nohello.models.run.scan_result import ScanResult
from webex_nohello.models.run.scan_state import ScanState
from webex_nohello.models.webex.person import Person
from webex_nohello.services.classify import ClassifierService
from webex_nohello.services.dispatch import DispatchService
from webex_nohello.services.scan import DEFAULT_LOOKBACK


def announce_window(state: ScanState, lookback: timedelta | None) -> None:
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


def report(result: ScanResult, *, store_path: str) -> None:
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

    report_candidates(result.candidates)
    report_skipped(result)

    ui.blank()
    ui.line(f"Read positions: {store_path}")


def report_candidates(candidates: tuple[Candidate, ...]) -> None:
    ui.blank()
    if not candidates:
        ui.success("No messages need looking at.")
        return

    ui.heading(f"{len(candidates)} message(s) would be examined by the classifier:")
    ui.blank()
    for candidate in candidates:
        report_candidate(candidate)


def report_candidate(candidate: Candidate) -> None:
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


def report_skipped(result: ScanResult) -> None:
    counts = result.counts_by_reason()
    if not counts:
        return

    ui.line(f"Skipped {len(result.skipped)} spaces:")
    for reason, count in sorted(counts.items(), key=lambda pair: -pair[1]):
        ui.line(f"    {count:>4}  {reason.description}")


def report_assessment(assessment: Assessment) -> None:
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


def explain(classifier: ClassifierService, candidate: Candidate, operator: Person) -> None:
    """Article IX.10: a verdict must be reproducible by hand."""
    ui.line("     --- prompt ---")
    for line_out in classifier.prompt_for(candidate, operator).splitlines():
        ui.line(f"     {line_out}")
    ui.line("     --- command ---")
    ui.line(f"     {shlex.join(classifier.command_for(candidate, operator))}")
    ui.blank()


def report_outcome(outcome: ReplyOutcome) -> None:
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


def preview_body(dispatcher: DispatchService, result: DispatchResult) -> None:
    """Article X.2: a dry run must show exactly what it would post, not a summary of it."""
    would_send = [one for one in result.outcomes if one.withheld is WithheldReason.DRY_RUN]
    if not would_send:
        return

    ui.heading("The reply that would be posted, in full:")
    ui.blank()
    for line_out in dispatcher.preview(would_send[0].assessment).splitlines():
        ui.line(f"  │ {line_out}" if line_out else "  │")
    ui.blank()


def report_cap(result: DispatchResult, settings: Settings) -> None:
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


def report_totals(result: DispatchResult, *, commit: bool) -> None:
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
