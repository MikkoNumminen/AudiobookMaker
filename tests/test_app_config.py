"""Unit tests for the user-config persistence module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src import app_config
from src.app_config import UserConfig, load, save


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Redirect CONFIG_DIR / CONFIG_FILE to a tmp_path for each test."""
    monkeypatch.setattr(app_config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(app_config, "CONFIG_FILE", tmp_path / "config.json")
    yield tmp_path


class TestDefaults:
    def test_defaults_are_sensible(self) -> None:
        cfg = UserConfig()
        assert cfg.engine_id == "edge"
        assert cfg.language == "fi"
        assert cfg.voice_id == ""
        assert cfg.speed == "+0%"
        assert cfg.reference_audio == ""
        assert cfg.voice_description == ""


class TestLoad:
    def test_load_returns_defaults_when_file_missing(self, tmp_config) -> None:
        cfg = load()
        assert cfg == UserConfig()

    def test_load_returns_defaults_on_invalid_json(self, tmp_config) -> None:
        (tmp_config / "config.json").write_text("not valid json")
        cfg = load()
        assert cfg == UserConfig()

    def test_load_reads_saved_values(self, tmp_config) -> None:
        (tmp_config / "config.json").write_text(
            json.dumps(
                {
                    "engine_id": "piper",
                    "language": "en",
                    "voice_id": "en_US-lessac-medium",
                    "speed": "+10%",
                }
            )
        )
        cfg = load()
        assert cfg.engine_id == "piper"
        assert cfg.language == "en"
        assert cfg.voice_id == "en_US-lessac-medium"
        assert cfg.speed == "+10%"

    def test_load_ignores_unknown_keys(self, tmp_config) -> None:
        (tmp_config / "config.json").write_text(
            json.dumps({"engine_id": "edge", "legacy_key": "whatever"})
        )
        cfg = load()
        assert cfg.engine_id == "edge"

    def test_load_ignores_non_string_values(self, tmp_config) -> None:
        # Defensive: a bad type in config must not crash the load.
        (tmp_config / "config.json").write_text(
            json.dumps({"engine_id": 123, "language": "fi"})
        )
        cfg = load()
        assert cfg.language == "fi"
        assert cfg.engine_id == "edge"  # default because 123 was rejected


class TestSave:
    def test_save_creates_directory_and_file(self, tmp_config) -> None:
        # Delete the tmp dir to make sure save() recreates it.
        import shutil

        shutil.rmtree(tmp_config)
        cfg = UserConfig(engine_id="piper", voice_id="fi_FI-harri-medium")
        save(cfg)
        loaded = load()
        assert loaded.engine_id == "piper"
        assert loaded.voice_id == "fi_FI-harri-medium"

    def test_save_roundtrip(self, tmp_config) -> None:
        original = UserConfig(
            engine_id="piper",
            language="en",
            voice_id="en_GB-alan-medium",
            speed="-25%",
            reference_audio="/tmp/ref.wav",
            voice_description="(warm baritone elderly male)",
        )
        save(original)
        assert load() == original

    def test_save_swallows_ioerror(self, tmp_config) -> None:
        # A broken filesystem should not crash the GUI.
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            save(UserConfig())  # should not raise
