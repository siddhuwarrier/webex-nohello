"""The `review` command: judge the classifier against real history.

Deliberately separate from `run` rather than a flag on it. `run` will eventually be able to
post, and a flag that says "ignore the read positions" combined with one that says "send for
real" would re-reply to a week of messages. This command has no such flag and never will:
it writes nothing and posts nothing, and that is a property of the code rather than of the
operator remembering which flags are safe together.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import typer

from webex_nohello import paths, ui
from webex_nohello.clock import system_clock
from webex_nohello.commands.progress import classification_progress, scan_progress
from webex_nohello.models.classify.assessment import Assessment
from webex_nohello.models.classify.verdict_kind import VerdictKind
from webex_nohello.models.review.historical_candidate import HistoricalCandidate
from webex_nohello.services.agent_cli import CHOICES, build_driver
from webex_nohello.services.auth import build_auth_service
from webex_nohello.services.classify import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    ClassifierService,
)
from webex_nohello.services.config import load_settings
from webex_nohello.services.review import (
    DEFAULT_MAX_MESSAGES,
    ReviewService,
)
from webex_nohello.services.webex import WebexService

# Measured, not guessed: see Appendix A. Used only to warn before spending.
SECONDS_PER_CALL = 6.0
USD_PER_CALL = 0.005
MINUTES_PER_HOUR = 60


def review(
    lookback_days: Annotated[int, typer.Option(help="How many days of history to judge.")] = 7,
    max_messages: Annotated[
        int, typer.Option(help="Stop after this many messages, to bound time and cost.")
    ] = DEFAULT_MAX_MESSAGES,
    max_spaces: Annotated[
        int | None, typer.Option(help="Only look at this many spaces, most recently active first.")
    ] = None,
    classifier: Annotated[
        str | None, typer.Option(help=f"Which CLI classifies: {', '.join(CHOICES)}.")
    ] = None,
    model: Annotated[str | None, typer.Option(help="Model for the classifier.")] = None,
    confidence: Annotated[
        float, typer.Option(help="Confidence at or above which a reply would be sent.")
    ] = DEFAULT_CONFIDENCE_THRESHOLD,
    output_json: Annotated[
        Path | None, typer.Option("--json", help="Write every verdict to this file for analysis.")
    ] = None,
    poll_minutes: Annotated[
        int,
        typer.Option(
            help=(
                "Assume a scheduled run every N minutes. A greeting superseded faster than "
                "that would never have been replied to."
            )
        ),
    ] = 15,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Classify recent messages and show what would have happened. Sends nothing."""
    session = build_auth_service().require()
    reader = WebexService(session.access_token)
    operator = reader.get_me()

    ui.line(f"Signed in as {operator.display_name} <{operator.primary_email}>")
    ui.line(f"Collecting inbound messages from the last {lookback_days} days...")
    ui.blank()

    collector = ReviewService(reader, system_clock)
    with scan_progress() as progress:
        candidates = collector.collect(
            operator,
            lookback=timedelta(days=lookback_days),
            max_spaces=max_spaces,
            max_messages=max_messages,
            progress=progress,
        )

    if not candidates:
        ui.success("No inbound messages found in that window.")
        return

    ui.line(f"Found {len(candidates)} message(s) to judge.")
    if len(candidates) >= max_messages:
        ui.warn(f"Stopped at the --max-messages limit of {max_messages}; there may be more.")
    _confirm_spend(len(candidates), skip=yes)

    settings = load_settings(paths.config_file())
    service = ClassifierService(
        build_driver(
            preference=classifier or settings.classifier,
            model=model or settings.classifier_model,
        ),
        confidence_threshold=confidence,
    )
    judged: list[tuple[HistoricalCandidate, Assessment]] = []
    with classification_progress(len(candidates)) as progress:
        for historical in candidates:
            progress.classifying(historical.candidate)
            judged.append((historical, service.assess(historical.candidate, operator)))

    _report(judged, confidence, timedelta(minutes=poll_minutes))
    if output_json is not None:
        _write_json(judged, output_json)


def _confirm_spend(count: int, *, skip: bool) -> None:
    minutes = count * SECONDS_PER_CALL / 60
    ui.indented(
        f"That is about {minutes:.0f} minute(s) and roughly ${count * USD_PER_CALL:.2f} "
        "of model usage."
    )
    ui.blank()
    if not skip:
        typer.confirm("Go ahead?", default=True, abort=True)
        ui.blank()


def _report(
    judged: list[tuple[HistoricalCandidate, Assessment]], threshold: float, poll: timedelta
) -> None:
    flagged = [(h, a) for h, a in judged if a.is_reply_warranted]
    # Article VI.2 means only the newest message in a space is ever a candidate, so a
    # greeting overtaken sooner than the poll interval is never seen at all.
    reachable = [(h, a) for h, a in flagged if h.would_be_seen_by_a_poll_every(poll)]
    superseded = [pair for pair in flagged if pair not in reachable]

    ui.blank()
    ui.heading("Every judgement, worst first")
    ui.blank()
    for historical, assessment in sorted(judged, key=lambda pair: _severity(pair[1])):
        _report_one(historical, assessment)

    ui.blank()
    ui.heading("Summary")
    for kind in VerdictKind:
        count = sum(1 for _, a in judged if a.verdict is not None and a.verdict.verdict is kind)
        ui.line(f"    {count:>4}  {kind}")
    ui.line(f"    {sum(1 for _, a in judged if a.was_prefiltered):>4}  skipped as too long")
    ui.line(f"    {sum(1 for _, a in judged if a.failure is not None):>4}  could not classify")

    ui.blank()
    if not flagged:
        ui.success(f"None of {len(judged)} messages would have been replied to.")
        return

    if reachable:
        ui.warn(
            f"{len(reachable)} of {len(judged)} would actually have been replied to, "
            f"polling every {int(poll.total_seconds() // 60)} minutes."
        )
        ui.indented(
            "Read those carefully. Each is a message a colleague would have received an "
            f"automated reply to, at the current threshold of {threshold}."
        )
    else:
        ui.success(
            f"None of {len(judged)} would actually have been replied to, polling every "
            f"{int(poll.total_seconds() // 60)} minutes."
        )

    if superseded:
        ui.blank()
        ui.line(
            f"{len(superseded)} looked replyable but were overtaken by a later message too "
            "quickly for a run to see them."
        )
        ui.indented(
            "Those are not misfires. Only the newest message in a space is ever a candidate, "
            "so a greeting followed shortly by a real question is never replied to. They would "
            "matter only if you polled more often than the gap shown against each."
        )


def _severity(assessment: Assessment) -> tuple[int, float]:
    """Sort would-reply first, most confident first, so misfires surface at the top."""
    if assessment.is_reply_warranted:
        return (0, -(assessment.verdict.confidence if assessment.verdict else 0.0))
    if assessment.failure is not None:
        return (1, 0.0)
    return (2, 0.0)


def _report_one(historical: HistoricalCandidate, assessment: Assessment) -> None:
    candidate = historical.candidate
    sender = candidate.sender_email or candidate.space.title or "unknown sender"
    when = candidate.message.created
    stamp = when.astimezone().strftime("%Y-%m-%d %H:%M") if when else "unknown time"

    if assessment.is_reply_warranted:
        ui.failure(f"WOULD REPLY  {sender}  {stamp}")
    elif assessment.failure is not None:
        ui.warn(f"unclassified  {sender}  {stamp}")
    else:
        ui.line(f"  ok          {sender}  {stamp}")

    ui.copyable(candidate.message.excerpt())
    ui.indented(
        "too long to be a greeting; no inference needed"
        if assessment.was_prefiltered
        else assessment.summary
    )
    ui.indented(f"{len(candidate.conversation)} message(s) of context, {_lifetime(historical)}")
    ui.blank()


def _lifetime(historical: HistoricalCandidate) -> str:
    if historical.superseded_after is None:
        return "still the newest message in that space"
    minutes = historical.superseded_after.total_seconds() / 60
    if minutes < 1:
        return "overtaken within a minute"
    if minutes < MINUTES_PER_HOUR:
        return f"overtaken after {minutes:.0f} minutes"
    return f"overtaken after {minutes / MINUTES_PER_HOUR:.1f} hours"


def _write_json(judged: list[tuple[HistoricalCandidate, Assessment]], destination: Path) -> None:
    """Full verdicts, for grepping and for comparing prompt versions against each other."""
    payload = [
        {
            "space_id": historical.candidate.space.id,
            "space_title": historical.candidate.space.title,
            "sender": historical.candidate.sender_email,
            "message_id": historical.candidate.message.id,
            "created": historical.candidate.message.created.isoformat()
            if historical.candidate.message.created
            else None,
            "text": historical.candidate.message.text,
            "context_size": len(historical.candidate.conversation),
            "seconds_until_superseded": (
                historical.superseded_after.total_seconds()
                if historical.superseded_after is not None
                else None
            ),
            "still_newest": historical.is_still_current,
            "verdict": assessment.verdict.verdict.value if assessment.verdict else None,
            "confidence": assessment.verdict.confidence if assessment.verdict else None,
            "reason": assessment.verdict.reason if assessment.verdict else None,
            "prefiltered": assessment.was_prefiltered,
            "failure": assessment.failure,
            "classified_as_replyable": assessment.is_reply_warranted,
        }
        for historical, assessment in judged
    ]
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    ui.blank()
    ui.success(f"Wrote {len(payload)} verdicts to {destination}")
    ui.warn("That file contains full message text. Delete it when you are done.")
