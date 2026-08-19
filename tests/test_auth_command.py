"""The `auth` commands, driven through Typer.

These assert the contract a script or scheduler depends on: what is printed, and the
exit code. The service is stubbed, so nothing touches Webex or the keychain.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from typer.testing import CliRunner

from conftest import NOW, make_credentials, make_tokens
from webex_nohello.cli import app
from webex_nohello.models.auth.credential_report import CredentialReport
from webex_nohello.models.auth.credential_state import CredentialState
from webex_nohello.models.auth.refresh_outcome import RefreshOutcome

runner = CliRunner()


class StubAuthService:
    """Only the methods the commands actually call."""

    def __init__(self, report: CredentialReport) -> None:
        self.report = report
        self.logouts = 0
        self.calls: list[str] = []
        self.refresh_outcome = RefreshOutcome(previous=make_tokens(), current=make_tokens())

    def verify(self) -> CredentialReport:
        self.calls.append("verify")
        return self.report

    def refresh_now(self) -> RefreshOutcome:
        self.calls.append("refresh_now")
        return self.refresh_outcome

    def inspect(self) -> CredentialReport:
        self.calls.append("inspect")
        return self.report

    def logout(self) -> None:
        self.logouts += 1


def install(monkeypatch: pytest.MonkeyPatch, service: StubAuthService) -> StubAuthService:
    monkeypatch.setattr("webex_nohello.commands.auth.build_auth_service", lambda: service)
    return service


def run_status(monkeypatch: pytest.MonkeyPatch, report: CredentialReport) -> tuple[int, str]:
    install(monkeypatch, StubAuthService(report))
    result = runner.invoke(app, ["auth", "status"])
    return result.exit_code, result.output


def report_for(state: CredentialState, **overrides: object) -> CredentialReport:
    credentials = make_credentials()
    detail: dict[str, object] = {
        "person_email": credentials.person_email,
        "person_display_name": credentials.person_display_name,
        "access_token_expires_at": NOW + timedelta(days=13),
        "refresh_token_expires_at": NOW + timedelta(days=89),
    }
    detail.update(overrides)
    return CredentialReport(state=state, **detail)  # type: ignore[arg-type]  # kwargs are checked by the tests below


class TestStatusExitCode:
    def test_a_verified_grant_exits_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        code, output = run_status(monkeypatch, report_for(CredentialState.READY))

        assert code == 0
        assert "Webex confirms" in output

    def test_being_signed_out_exits_non_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        code, output = run_status(monkeypatch, CredentialReport(state=CredentialState.SIGNED_OUT))

        assert code == 1
        assert "Not signed in" in output
        assert "auth login" in output

    def test_a_rejected_grant_exits_non_zero_and_shows_why(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The masked-secret case. The exit code is what a scheduler acts on."""
        code, output = run_status(
            monkeypatch,
            report_for(
                CredentialState.REJECTED,
                rejection="Webex refused to read your own profile: [401] Unauthorized",
            ),
        )

        assert code == 1
        assert "401" in output
        assert "auth logout" in output

    def test_missing_scopes_exits_non_zero_and_names_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        code, output = run_status(
            monkeypatch,
            report_for(CredentialState.MISSING_SCOPES, absent_scopes=("spark:messages_write",)),
        )

        assert code == 1
        assert "spark:messages_write" in output

    def test_an_expired_refresh_token_exits_non_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        code, output = run_status(monkeypatch, report_for(CredentialState.REFRESH_EXPIRED))

        assert code == 1
        assert "auth login" in output


def test_status_asks_webex_rather_than_reading_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Article XII.5: readiness is only ever claimed on the strength of a live call."""
    service = install(monkeypatch, StubAuthService(report_for(CredentialState.READY)))

    runner.invoke(app, ["auth", "status"])

    assert service.calls == ["verify"]


class TestRefreshCommand:
    def _service(self, report: CredentialReport) -> StubAuthService:
        service = StubAuthService(report)
        service.refresh_outcome = RefreshOutcome(
            previous=make_tokens(access_valid_for=timedelta(hours=6)),
            current=make_tokens(access_token="access-2", refresh_token="refresh-2"),
        )
        return service

    def test_shows_the_expiry_before_and_after(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without the comparison the command proves nothing to whoever ran it."""
        install(monkeypatch, self._service(report_for(CredentialState.READY)))

        result = runner.invoke(app, ["auth", "refresh"])

        assert result.exit_code == 0
        assert "Refreshed." in result.output
        assert "was" in result.output
        assert "now" in result.output

    def test_verifies_the_new_token_rather_than_assuming_it_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = install(monkeypatch, self._service(report_for(CredentialState.READY)))

        runner.invoke(app, ["auth", "refresh"])

        assert service.calls == ["refresh_now", "verify"]

    def test_exits_non_zero_when_the_refreshed_token_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        report = report_for(CredentialState.REJECTED, rejection="[401] Unauthorized")
        install(monkeypatch, self._service(report))

        result = runner.invoke(app, ["auth", "refresh"])

        assert result.exit_code == 1

    def test_warns_when_webex_did_not_rotate_the_refresh_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = StubAuthService(report_for(CredentialState.READY))
        service.refresh_outcome = RefreshOutcome(
            previous=make_tokens(), current=make_tokens(access_token="access-2")
        )
        install(monkeypatch, service)

        result = runner.invoke(app, ["auth", "refresh"])

        assert "rather than rotating" in result.output


def test_logout_reports_success_and_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    service = install(monkeypatch, StubAuthService(report_for(CredentialState.READY)))

    result = runner.invoke(app, ["auth", "logout"])

    assert result.exit_code == 0
    assert service.logouts == 1
    assert "deleted" in result.output


def test_the_top_level_help_warns_replies_come_from_your_account() -> None:
    """The warning is the single most important thing this program tells anyone."""
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "your own account" in result.output


def test_auth_lists_its_three_commands() -> None:
    result = runner.invoke(app, ["auth", "--help"])

    for command in ("login", "status", "logout"):
        assert command in result.output
