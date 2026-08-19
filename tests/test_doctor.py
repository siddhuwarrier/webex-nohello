"""DoctorService: every check, and the failure/warning distinction.

The distinction is what `schedule install` gates on, so getting it wrong either arms a
broken schedule or refuses a working one.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from conftest import NOW, make_credentials
from webex_nohello.models.auth.credential_report import CredentialReport
from webex_nohello.models.auth.credential_state import CredentialState
from webex_nohello.models.doctor.check import Check
from webex_nohello.models.doctor.check_outcome import CheckOutcome
from webex_nohello.models.doctor.health_report import HealthReport
from webex_nohello.models.doctor.preflight_paths import PreflightPaths
from webex_nohello.models.errors.webex_api_error import WebexApiError
from webex_nohello.services.doctor import DoctorService


def healthy_report() -> CredentialReport:
    credentials = make_credentials()
    return CredentialReport(
        state=CredentialState.READY,
        person_email=credentials.person_email,
        person_display_name=credentials.person_display_name,
        access_token_expires_at=NOW + timedelta(days=13),
        refresh_token_expires_at=NOW + timedelta(days=89),
    )


class StubCredentials:
    def __init__(self, report: CredentialReport | None = None, raises: Exception | None = None):
        self._report = report if report is not None else healthy_report()
        self._raises = raises

    def verify(self) -> CredentialReport:
        if self._raises is not None:
            raise self._raises
        return self._report


def locations(tmp_path: Path) -> PreflightPaths:
    return PreflightPaths(
        config=tmp_path / "config.toml",
        reply_template=tmp_path / "reply.md",
        state_directory=tmp_path / "state",
        scan_state=tmp_path / "state" / "scan-state.json",
        paused=tmp_path / "state" / "PAUSED",
    )


def examine(
    tmp_path: Path,
    *,
    credentials: StubCredentials | None = None,
    probe: object = "OK",
) -> HealthReport:
    def probe_ok() -> str:
        if isinstance(probe, Exception):
            raise probe
        return str(probe)

    return DoctorService(
        credentials if credentials is not None else StubCredentials(),
        locations(tmp_path),
        probe_inference=probe_ok,
    ).examine()


def find(report: HealthReport, name: str) -> Check:
    return next(check for check in report.checks if check.name == name)


class TestHealthyInstall:
    def test_a_clean_install_passes(self, tmp_path: Path) -> None:
        report = examine(tmp_path)

        assert report.is_healthy
        assert report.failures == ()

    def test_an_empty_allow_list_warns_rather_than_fails(self, tmp_path: Path) -> None:
        """An install that replies to nobody works exactly as configured."""
        (tmp_path / "config.toml").write_text("opt_in_only = true\n", encoding="utf-8")

        report = examine(tmp_path)

        assert report.is_healthy
        assert find(report, "who gets replies").outcome is CheckOutcome.WARNED

    def test_replying_to_anyone_is_reported_without_complaint(self, tmp_path: Path) -> None:
        (tmp_path / "config.toml").write_text("opt_in_only = false\n", encoding="utf-8")

        report = examine(tmp_path)

        assert find(report, "who gets replies").outcome is CheckOutcome.PASSED


class TestClassifier:
    def test_a_working_classifier_passes(self, tmp_path: Path) -> None:
        assert find(examine(tmp_path), "classifier").outcome is CheckOutcome.PASSED

    def test_a_broken_classifier_fails_the_report(self, tmp_path: Path) -> None:
        """A schedule with no classifier would run forever and do nothing useful."""
        report = examine(tmp_path, probe=WebexApiError("claude is not on PATH"))

        assert not report.is_healthy
        assert find(report, "classifier").outcome is CheckOutcome.FAILED

    def test_skipping_the_probe_warns_rather_than_passing(self, tmp_path: Path) -> None:
        """Not checking is not the same as checking successfully."""
        report = DoctorService(
            StubCredentials(), locations(tmp_path), probe_inference=None
        ).examine()

        assert find(report, "classifier").outcome is CheckOutcome.WARNED
        assert report.is_healthy


class TestWebex:
    def test_being_signed_out_fails(self, tmp_path: Path) -> None:
        stub = StubCredentials(CredentialReport(state=CredentialState.SIGNED_OUT))

        report = examine(tmp_path, credentials=stub)

        assert not report.is_healthy
        assert "auth login" in (find(report, "webex credentials").remediation or "")

    def test_rejected_credentials_fail_the_connection_check(self, tmp_path: Path) -> None:
        """The masked-secret case: sound-looking record, token Webex refuses."""
        stub = StubCredentials(
            CredentialReport(
                state=CredentialState.REJECTED,
                person_email="me@example.com",
                person_display_name="Me",
                access_token_expires_at=NOW + timedelta(days=13),
                refresh_token_expires_at=NOW + timedelta(days=89),
                rejection="[401] Unauthorized",
            )
        )

        report = examine(tmp_path, credentials=stub)

        assert not report.is_healthy
        assert "401" in find(report, "webex connection").detail

    def test_a_raising_check_is_reported_not_propagated(self, tmp_path: Path) -> None:
        """One broken check must not deny the operator the other seven."""
        stub = StubCredentials(raises=WebexApiError("the keychain is locked"))

        report = examine(tmp_path, credentials=stub)

        assert not report.is_healthy
        assert len(report.checks) > 1


class TestConfigAndTemplate:
    def test_a_broken_config_fails_and_names_the_key(self, tmp_path: Path) -> None:
        (tmp_path / "config.toml").write_text("cooldown_days = 30\n", encoding="utf-8")

        report = examine(tmp_path)

        assert not report.is_healthy
        assert "cooldown_minutes" in find(report, "config").detail

    def test_unparseable_toml_fails(self, tmp_path: Path) -> None:
        (tmp_path / "config.toml").write_text("this is not = = toml\n", encoding="utf-8")

        assert not examine(tmp_path).is_healthy

    def test_a_template_with_an_unknown_placeholder_fails(self, tmp_path: Path) -> None:
        """Only rendering catches this; loading the file would not."""
        (tmp_path / "reply.md").write_text("Hi {their_name}\n", encoding="utf-8")

        report = examine(tmp_path)

        assert not report.is_healthy
        assert "their_name" in find(report, "reply text").detail

    def test_an_empty_template_fails(self, tmp_path: Path) -> None:
        (tmp_path / "reply.md").write_text("  \n", encoding="utf-8")

        assert not examine(tmp_path).is_healthy

    def test_a_valid_custom_template_passes(self, tmp_path: Path) -> None:
        (tmp_path / "reply.md").write_text("Hi {sender_first_name}\n", encoding="utf-8")

        assert find(examine(tmp_path), "reply text").outcome is CheckOutcome.PASSED


class TestStateDirectory:
    def test_a_writable_directory_passes(self, tmp_path: Path) -> None:
        assert find(examine(tmp_path), "state directory").outcome is CheckOutcome.PASSED

    def test_the_directory_is_created_if_absent(self, tmp_path: Path) -> None:
        examine(tmp_path)

        assert (tmp_path / "state").is_dir()

    def test_an_unreadable_state_file_fails(self, tmp_path: Path) -> None:
        """Treating it as empty would put the whole message history back in scope."""
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "scan-state.json").write_text("{ nonsense", encoding="utf-8")

        report = examine(tmp_path)

        assert not report.is_healthy
        assert find(report, "state directory").outcome is CheckOutcome.FAILED

    @pytest.mark.skipif(Path("/").stat().st_uid != 0, reason="needs a root-owned filesystem")
    def test_an_unwritable_directory_fails(self) -> None:
        service = DoctorService(
            StubCredentials(),
            PreflightPaths(
                config=Path("/nope/config.toml"),
                reply_template=Path("/nope/reply.md"),
                state_directory=Path("/nope/state"),
                scan_state=Path("/nope/state/scan-state.json"),
                paused=Path("/nope/state/PAUSED"),
            ),
            probe_inference=lambda: "OK",
        )

        assert not service.examine().is_healthy


class TestKillSwitch:
    def test_an_engaged_kill_switch_warns_rather_than_fails(self, tmp_path: Path) -> None:
        """The install is healthy; it is just deliberately stopped."""
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "PAUSED").write_text("", encoding="utf-8")

        report = examine(tmp_path)

        assert report.is_healthy
        assert find(report, "kill switch").outcome is CheckOutcome.WARNED

    def test_the_warning_says_how_to_resume(self, tmp_path: Path) -> None:
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "PAUSED").write_text("", encoding="utf-8")

        assert "Delete" in (find(examine(tmp_path), "kill switch").remediation or "")


def test_every_failure_carries_remediation(tmp_path: Path) -> None:
    """Article XII.2: a failure that does not say what to do is not much use."""
    (tmp_path / "config.toml").write_text("cooldown_days = 30\n", encoding="utf-8")
    (tmp_path / "reply.md").write_text("Hi {nope}\n", encoding="utf-8")

    report = examine(
        tmp_path,
        credentials=StubCredentials(CredentialReport(state=CredentialState.SIGNED_OUT)),
        probe=WebexApiError("claude is missing"),
    )

    assert report.failures
    for failure in report.failures:
        assert failure.remediation, f"{failure.name} failed without saying what to do"
