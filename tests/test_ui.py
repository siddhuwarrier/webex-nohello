"""Output: timestamped in a log, clean in a terminal.

The distinction matters because the same lines serve two audiences. An operator watching knows
what time it is; a log of a job polling every ten minutes has nothing else to say when
anything happened.
"""

from __future__ import annotations

import re

import pytest

from webex_nohello import ui
from webex_nohello.models.errors.webex_api_error import WebexApiError

# Matches the log prefix: 2026-08-19 14:25:24+0100
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4}  ")


@pytest.fixture
def logging_to_a_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "_is_logging", lambda: True)


@pytest.fixture
def watching_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "_is_logging", lambda: False)


class TestLogOutput:
    def test_a_line_is_timestamped(
        self, logging_to_a_file: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ui.line("something happened")

        out = capsys.readouterr().out
        assert STAMP.match(out)
        assert out.rstrip().endswith("something happened")

    @pytest.mark.parametrize(
        "emit",
        [ui.line, ui.heading, ui.success, ui.warn, ui.copyable, ui.bullet, ui.indented],
    )
    def test_every_helper_is_timestamped(
        self,
        logging_to_a_file: None,
        capsys: pytest.CaptureFixture[str],
        emit: object,
    ) -> None:
        """One funnel, so a new helper cannot silently skip the prefix."""
        emit("text")  # type: ignore[operator]  # parametrised over same-shaped callables

        assert STAMP.match(capsys.readouterr().out)

    def test_failure_goes_to_stderr_and_is_timestamped(
        self, logging_to_a_file: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ui.failure("it broke")

        captured = capsys.readouterr()
        assert captured.out == ""
        assert STAMP.match(captured.err)

    def test_a_blank_line_stays_blank(
        self, logging_to_a_file: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A lone timestamp is noise, and the blanks are what make a long log skimmable."""
        ui.blank()

        assert capsys.readouterr().out == "\n"

    def test_no_escape_codes_reach_the_file(
        self, logging_to_a_file: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ui.success("green in a terminal, plain in a file")

        assert "\x1b[" not in capsys.readouterr().out

    def test_a_run_separator_is_written(
        self, logging_to_a_file: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ui.run_separator("run --commit")

        out = capsys.readouterr().out
        assert "run --commit" in out
        assert "=" * 20 in out


class TestTerminalOutput:
    def test_a_line_is_not_timestamped(
        self, watching_a_terminal: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The operator is watching, and knows when they pressed return."""
        ui.line("something happened")

        assert capsys.readouterr().out == "something happened\n"

    def test_no_run_separator_interrupts_an_interactive_run(
        self, watching_a_terminal: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ui.run_separator("run")

        assert capsys.readouterr().out == ""


class TestErrorRendering:
    def test_the_remediation_follows_the_message(
        self, logging_to_a_file: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ui.render_error(WebexApiError("it broke", remediation="try turning it off and on"))

        err = capsys.readouterr().err
        assert "it broke" in err
        assert "try turning it off and on" in err

    def test_an_error_without_remediation_prints_only_the_message(
        self, logging_to_a_file: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ui.render_error(WebexApiError("it broke"))

        assert capsys.readouterr().err.count("\n") == 1
