"""Article XI: what the config file may say, and what happens when it says something wrong.

A silently ignored setting is the failure mode these guard against. Every rail in Article X
is configurable, so a key that is misspelt, renamed or blank must be reported rather than
quietly dropped — the operator would otherwise believe a limit is in force that is not.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from webex_nohello.models.config.settings import Settings
from webex_nohello.models.errors.webex_nohello_error import WebexNoHelloError
from webex_nohello.services.config import STARTER_CONFIG, load_settings, write_starter_config


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


class TestStarterConfig:
    def test_it_is_valid_toml_and_valid_settings(self) -> None:
        """It is prose most of the time, so nothing else proves the example lines still parse."""
        assert Settings.model_validate(tomllib.loads(STARTER_CONFIG)) == Settings()

    def test_it_is_not_overwritten_without_being_asked(self, tmp_path: Path) -> None:
        """It can name real colleagues; losing an allow_list silently would be a bad way out."""
        path = write(tmp_path, "opt_in_only = false\n")

        assert not write_starter_config(path)
        assert path.read_text(encoding="utf-8") == "opt_in_only = false\n"

    def test_force_overwrites(self, tmp_path: Path) -> None:
        path = write(tmp_path, "opt_in_only = false\n")

        assert write_starter_config(path, overwrite=True)
        assert load_settings(path).opt_in_only


class TestLoading:
    def test_no_file_means_defaults(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "absent.toml") == Settings()

    def test_an_unknown_key_is_refused(self, tmp_path: Path) -> None:
        """extra="forbid": a misspelt deny_list that quietly did nothing would deny nobody."""
        path = write(tmp_path, "denylist = []\n")

        with pytest.raises(WebexNoHelloError) as caught:
            load_settings(path)

        assert "denylist" in caught.value.message

    def test_broken_toml_names_the_file(self, tmp_path: Path) -> None:
        path = write(tmp_path, "this is not = = toml\n")

        with pytest.raises(WebexNoHelloError) as caught:
            load_settings(path)

        assert str(path) in caught.value.message

    def test_a_renamed_key_names_its_replacement(self, tmp_path: Path) -> None:
        path = write(tmp_path, "cooldown_days = 30\n")

        with pytest.raises(WebexNoHelloError) as caught:
            load_settings(path)

        assert "cooldown_minutes" in caught.value.message


class TestReplyFile:
    def test_it_is_unset_by_default(self) -> None:
        assert Settings().reply_file is None

    def test_a_path_is_kept_as_written(self, tmp_path: Path) -> None:
        """Unresolved on purpose: resolution needs the config's own directory, not the cwd."""
        path = write(tmp_path, 'reply_file = "prose/mine.md"\n')

        assert load_settings(path).reply_file == Path("prose/mine.md")

    def test_a_blank_path_is_refused(self, tmp_path: Path) -> None:
        """It would otherwise resolve to the config directory itself."""
        path = write(tmp_path, 'reply_file = ""\n')

        with pytest.raises(WebexNoHelloError) as caught:
            load_settings(path)

        assert "reply_file" in caught.value.message
