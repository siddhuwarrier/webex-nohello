"""Preflight: is this install fit to run unattended?

Article XII. Every check runs independently and none aborts the others, because the operator
wants the whole picture in one pass rather than to fix one thing, re-run, and find the next.

The distinction the report turns on is failure versus warning. An install with an empty
allow_list works perfectly and sends nothing; that is a warning. A missing classifier CLI
means a scheduled run would do nothing useful forever; that is a failure.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from webex_nohello.models.auth.credential_report import CredentialReport
from webex_nohello.models.auth.credential_state import CredentialState
from webex_nohello.models.config.settings import Settings
from webex_nohello.models.doctor.check import Check
from webex_nohello.models.doctor.health_report import HealthReport
from webex_nohello.models.doctor.preflight_paths import PreflightPaths
from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError
from webex_nohello.models.webex.person import Person
from webex_nohello.services.config import load_settings
from webex_nohello.services.lock import is_paused
from webex_nohello.services.reply_template import load_reply, render
from webex_nohello.services.state import FileStateStore

INSTALL_CLAUDE = "Install Claude Code and sign in: https://docs.claude.com/en/docs/claude-code"


class CredentialChecker(Protocol):
    def verify(self) -> CredentialReport: ...


class DoctorService:
    def __init__(
        self,
        credentials: CredentialChecker,
        locations: PreflightPaths,
        *,
        probe_inference: Callable[[], str] | None = None,
    ) -> None:
        self._credentials = credentials
        self._paths = locations
        self._probe_inference = probe_inference

    def examine(self) -> HealthReport:
        settings = self._settings_or_none()
        return HealthReport(
            checks=(
                self._check_kill_switch(),
                self._check_classifier(),
                *self._check_webex(),
                self._check_config(),
                self._check_reply_text(settings),
                self._check_state_directory(),
                *(() if settings is None else (self._check_anything_can_be_sent(settings),)),
            )
        )

    def _check_kill_switch(self) -> Check:
        """First, because everything below is moot while it is engaged."""
        if not is_paused(self._paths.paused):
            return Check.passed("kill switch", "not engaged")
        return Check.warned(
            "kill switch",
            f"engaged: {self._paths.paused} exists, so every run will stop immediately",
            f"Delete {self._paths.paused} to resume.",
        )

    def _check_classifier(self) -> Check:
        if self._probe_inference is None:
            return Check.warned(
                "classifier", "not checked", "Drop --skip-inference to prove it works."
            )
        try:
            answer = self._probe_inference()
        except WebexNoHelloError as exc:
            return Check.failed("classifier", exc.message, exc.remediation or INSTALL_CLAUDE)
        return Check.passed("classifier", answer.strip()[:90])

    def _check_webex(self) -> tuple[Check, ...]:
        """One live call, two findings: are the credentials sound, and does Webex accept them.

        Article XII.1 lists those separately and they fail for different reasons, but making
        two calls to report them would be wasteful and could disagree with itself.
        """
        try:
            report = self._credentials.verify()
        except WebexNoHelloError as exc:
            return (
                Check.failed(
                    "webex credentials",
                    exc.message,
                    exc.remediation or "Run 'webex-nohello auth login'.",
                ),
            )

        if report.state is CredentialState.SIGNED_OUT:
            return (
                Check.failed(
                    "webex credentials",
                    "not signed in",
                    "Run 'webex-nohello auth login'. It will walk you through registering "
                    "a Webex integration.",
                ),
            )

        expiry = Check.passed(
            "webex credentials",
            f"{report.person_display_name} <{report.person_email}>; "
            f"refresh token until {report.refresh_token_expires_at:%Y-%m-%d}",
        )

        if report.is_ready:
            return (expiry, Check.passed("webex connection", "a live read succeeded"))

        detail = report.rejection or f"credentials are {report.state}"
        return (
            expiry,
            Check.failed(
                "webex connection",
                detail,
                "Run 'webex-nohello auth logout' then 'webex-nohello auth login'.",
            ),
        )

    def _check_config(self) -> Check:
        try:
            settings = load_settings(self._paths.config)
        except WebexNoHelloError as exc:
            return Check.failed("config", exc.message, exc.remediation or "Correct the file.")

        where = self._paths.config if self._paths.config.exists() else "defaults (no file)"
        return Check.passed(
            "config",
            f"{where}; cooldown {settings.cooldown_minutes}m, "
            f"cap {settings.max_replies_per_run}/run, confidence {settings.confidence_threshold}",
        )

    def _check_reply_text(self, settings: Settings | None) -> Check:
        """Rendered, not merely read: an unknown placeholder only fails at render time.

        Names the file either way. The commonest confusion this can clear up is an operator
        editing one reply.md while `reply_file` points at another.
        """
        if settings is None:
            return Check.warned(
                "reply text",
                "not checked: the config could not be read",
                "Fix the config first; it says which file the reply text is in.",
            )

        try:
            source = load_reply(settings.reply_file, default_path=self._paths.reply_template)
            rendered = render(source.text, _EXAMPLE_SENDER)
        except WebexNoHelloError as exc:
            return Check.failed(
                "reply text", exc.message, exc.remediation or "Correct the reply text."
            )

        where = source.path if source.is_customised else f"built-in default (no {source.path})"
        return Check.passed("reply text", f"{where}; renders to {len(rendered)} characters")

    def _check_state_directory(self) -> Check:
        """Writability is proven by writing. A reply that cannot be recorded is not sent."""
        try:
            self._paths.state_directory.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(dir=self._paths.state_directory, prefix=".doctor-")
            os.close(handle)
            Path(name).unlink()
        except OSError as exc:
            return Check.failed(
                "state directory",
                f"{self._paths.state_directory} is not writable: {exc}",
                "Fix the permissions. Without it, replies cannot be recorded, and a reply "
                "that cannot be recorded is never sent.",
            )

        try:
            state = FileStateStore(self._paths.scan_state).load()
        except WebexNoHelloError as exc:
            return Check.failed(
                "state directory", exc.message, exc.remediation or "Inspect the state file."
            )

        tracked = len(state.marks)
        seen = "never run" if state.is_first_run else f"{tracked} spaces tracked"
        return Check.passed("state directory", f"{self._paths.state_directory} writable; {seen}")

    def _check_anything_can_be_sent(self, settings: Settings) -> Check:
        """Not a failure. An install that replies to nobody is working exactly as configured."""
        if not settings.opt_in_only:
            return Check.passed(
                "who gets replies",
                f"anyone not on the deny list ({len(settings.deny_list)} denied)",
            )
        if settings.allow_list:
            return Check.passed("who gets replies", f"{len(settings.allow_list)} on the allow list")
        return Check.warned(
            "who gets replies",
            "nobody: opt_in_only is on and allow_list is empty",
            f"That is the default and is deliberate. Add an address to {self._paths.config}.",
        )

    def _settings_or_none(self) -> Settings | None:
        try:
            return load_settings(self._paths.config)
        except WebexNoHelloError:
            # Already reported by _check_config; nothing is gained by saying it twice.
            return None


_EXAMPLE_SENDER = Person(
    id="example", emails=["colleague@example.com"], display_name="Example Colleague"
)
