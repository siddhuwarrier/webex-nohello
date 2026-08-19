"""Choosing a classifier CLI, and the codex driver's isolated home.

The home isolation is the security-relevant part: it is what stops codex loading the
operator's plugins, and therefore their Webex MCP server, into a classifier that Article IX.4
says must have no tools at all.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from webex_nohello.services.agent_cli import CHOICES, build_driver, installed_classifiers
from webex_nohello.services.claude_cli import ClaudeDriver
from webex_nohello.services.codex_cli import AUTH_FILENAME, CodexDriver
from webex_nohello.services.inference import InferenceError

SYSTEM = "You classify messages."
PROMPT = "Classify this."


def installed(*executables: str) -> Callable[[str], bool]:
    """State what is on PATH, so the suite does not depend on the developer's machine."""
    return lambda name: name in executables


BOTH = installed("claude", "codex")
NEITHER = installed()


def signed_in_codex(tmp_path: Path) -> Path:
    real = tmp_path / "real-codex"
    real.mkdir()
    (real / AUTH_FILENAME).write_text(json.dumps({"token": "not-a-real-token"}), encoding="utf-8")
    return real


def codex_driver(tmp_path: Path, **kwargs: object) -> CodexDriver:
    return CodexDriver(
        home=tmp_path / "isolated",
        real_home=signed_in_codex(tmp_path),
        **kwargs,  # type: ignore[arg-type]  # keyword pass-through for model/executable
    )


class TestChoosingADriver:
    def test_the_choices_are_the_documented_three(self) -> None:
        assert CHOICES == ("auto", "claude", "codex")

    def test_claude_can_be_chosen_explicitly(self) -> None:
        assert isinstance(build_driver(preference="claude", is_installed=BOTH), ClaudeDriver)

    def test_codex_can_be_chosen_explicitly(self) -> None:
        assert isinstance(build_driver(preference="codex", is_installed=BOTH), CodexDriver)

    def test_auto_prefers_claude_when_both_are_installed(self) -> None:
        """Not a judgement on the models: claude can be told to expose no tools directly."""
        assert isinstance(build_driver(preference="auto", is_installed=BOTH), ClaudeDriver)

    def test_auto_falls_back_to_codex(self) -> None:
        driver = build_driver(preference="auto", is_installed=installed("codex"))

        assert isinstance(driver, CodexDriver)

    def test_auto_with_neither_installed_says_how_to_install_both(self) -> None:
        with pytest.raises(InferenceError) as caught:
            build_driver(preference="auto", is_installed=NEITHER)

        assert caught.value.remediation is not None
        assert "claude" in caught.value.remediation.lower()
        assert "codex" in caught.value.remediation.lower()

    def test_choosing_one_that_is_not_installed_fails_immediately(self) -> None:
        """Rather than on the first classification, halfway through a run."""
        with pytest.raises(InferenceError) as caught:
            build_driver(preference="codex", is_installed=installed("claude"))

        assert "not on PATH" in caught.value.message

    def test_the_missing_cli_error_mentions_the_scheduled_path(self) -> None:
        """Because that is the case where it is least obvious what went wrong."""
        with pytest.raises(InferenceError) as caught:
            build_driver(preference="claude", is_installed=NEITHER)

        assert caught.value.remediation is not None
        assert "PATH" in caught.value.remediation

    def test_an_unknown_choice_is_refused_and_lists_the_valid_ones(self) -> None:
        with pytest.raises(InferenceError) as caught:
            build_driver(preference="gemini", is_installed=BOTH)

        assert caught.value.remediation is not None
        for choice in CHOICES:
            assert choice in caught.value.remediation

    def test_each_driver_reports_which_model_it_will_use(self) -> None:
        assert "haiku" in build_driver(preference="claude", is_installed=BOTH).name
        codex = build_driver(preference="codex", model="gpt-5-mini", is_installed=BOTH)
        assert "gpt-5-mini" in codex.name

    def test_codex_says_default_model_when_none_is_configured(self) -> None:
        """This program will not guess at a model name it has not verified."""
        assert "default model" in build_driver(preference="codex", is_installed=BOTH).name


class TestReportingWhatIsInstalled:
    """`auth login` says which CLI will judge messages, so it must not raise when none is."""

    def test_both_are_listed_with_claude_first(self) -> None:
        assert installed_classifiers(BOTH) == ("claude", "codex")

    def test_only_what_is_present_is_listed(self) -> None:
        assert installed_classifiers(installed("codex")) == ("codex",)

    def test_neither_is_an_empty_tuple_rather_than_an_error(self) -> None:
        assert installed_classifiers(NEITHER) == ()


class TestCodexCommand:
    def test_the_sandbox_is_read_only(self, tmp_path: Path) -> None:
        command = codex_driver(tmp_path).command_for(PROMPT, SYSTEM)

        assert "--sandbox" in command
        assert command[command.index("--sandbox") + 1] == "read-only"

    def test_the_git_repo_check_is_skipped(self, tmp_path: Path) -> None:
        """A scheduled run has no meaningful working directory."""
        assert "--skip-git-repo-check" in codex_driver(tmp_path).command_for(PROMPT, SYSTEM)

    def test_the_answer_is_read_from_a_file_not_stdout(self, tmp_path: Path) -> None:
        """stdout carries session preamble and log lines that would need stripping."""
        assert "--output-last-message" in codex_driver(tmp_path).command_for(PROMPT, SYSTEM)

    def test_the_system_prompt_is_prepended(self, tmp_path: Path) -> None:
        """codex has no separate system prompt flag."""
        command = codex_driver(tmp_path).command_for(PROMPT, SYSTEM)

        combined = next(part for part in command if PROMPT in part)
        assert combined.startswith(SYSTEM)

    def test_no_model_is_passed_unless_configured(self, tmp_path: Path) -> None:
        assert "--model" not in codex_driver(tmp_path).command_for(PROMPT, SYSTEM)

    def test_a_configured_model_is_passed(self, tmp_path: Path) -> None:
        command = codex_driver(tmp_path, model="gpt-5-mini").command_for(PROMPT, SYSTEM)

        assert command[command.index("--model") + 1] == "gpt-5-mini"

    def test_the_displayed_command_does_not_name_a_temporary_path(self, tmp_path: Path) -> None:
        """`--explain` output should be readable, not full of run-specific paths."""
        command = codex_driver(tmp_path).command_for(PROMPT, SYSTEM)

        assert "<answer.txt>" in command


class TestCodexIsolatedHome:
    def test_the_home_is_created_with_the_credentials_symlinked(self, tmp_path: Path) -> None:
        real = signed_in_codex(tmp_path)
        driver = CodexDriver(home=tmp_path / "isolated", real_home=real)

        driver._prepare_home()  # the isolation is the point of the test

        link = tmp_path / "isolated" / AUTH_FILENAME
        assert link.is_symlink()
        assert link.readlink() == real / AUTH_FILENAME

    def test_the_credentials_are_never_copied(self, tmp_path: Path) -> None:
        """A second copy of an auth token on disk is worse than the problem being solved."""
        real = signed_in_codex(tmp_path)
        driver = CodexDriver(home=tmp_path / "isolated", real_home=real)

        driver._prepare_home()

        link = tmp_path / "isolated" / AUTH_FILENAME
        assert link.is_symlink(), "a real file here would be a duplicated credential"

    def test_the_isolated_home_holds_no_config(self, tmp_path: Path) -> None:
        """No config means no plugins, which is the only way codex exposes no tools."""
        real = signed_in_codex(tmp_path)
        driver = CodexDriver(home=tmp_path / "isolated", real_home=real)

        driver._prepare_home()

        assert not (tmp_path / "isolated" / "config.toml").exists()

    def test_preparing_twice_is_harmless(self, tmp_path: Path) -> None:
        real = signed_in_codex(tmp_path)
        driver = CodexDriver(home=tmp_path / "isolated", real_home=real)

        driver._prepare_home()
        driver._prepare_home()

        assert (tmp_path / "isolated" / AUTH_FILENAME).is_symlink()

    def test_a_stale_link_is_replaced(self, tmp_path: Path) -> None:
        """The real home can move; a dangling link would fail confusingly."""
        real = signed_in_codex(tmp_path)
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        (isolated / AUTH_FILENAME).symlink_to(tmp_path / "somewhere-else" / AUTH_FILENAME)

        CodexDriver(home=isolated, real_home=real)._prepare_home()

        assert (isolated / AUTH_FILENAME).readlink() == real / AUTH_FILENAME

    def test_not_being_signed_in_says_so(self, tmp_path: Path) -> None:
        empty = tmp_path / "no-codex"
        empty.mkdir()

        with pytest.raises(InferenceError) as caught:
            CodexDriver(home=tmp_path / "isolated", real_home=empty)._prepare_home()

        assert "not signed in" in caught.value.message
        assert caught.value.remediation is not None
        assert "codex" in caught.value.remediation

    def test_a_real_file_where_the_link_belongs_is_refused(self, tmp_path: Path) -> None:
        """Rather than silently deleting something the operator may have put there."""
        real = signed_in_codex(tmp_path)
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        (isolated / AUTH_FILENAME).write_text("{}", encoding="utf-8")

        with pytest.raises(InferenceError) as caught:
            CodexDriver(home=isolated, real_home=real)._prepare_home()

        assert "not a symlink" in caught.value.message
