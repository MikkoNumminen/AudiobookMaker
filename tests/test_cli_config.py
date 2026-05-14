"""Tests for `audiobookmaker config` subcommand (src/cli/config.py).

Uses subprocess so the real argument-parsing and dispatch layer runs.
Each test that touches disk passes a temp USERPROFILE/HOME so developer
config is never mutated.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helper — mirrors _cli() in test_cli.py but lives here to stay independent.
# ---------------------------------------------------------------------------

def _cli(*args: str, env: dict | None = None):
    import subprocess

    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
        env=merged,
    )


def _home_env(tmp_path: Path) -> dict:
    """Return env vars that redirect Path.home() to tmp_path."""
    return {
        "USERPROFILE": str(tmp_path),   # Windows
        "HOME": str(tmp_path),          # POSIX
    }


# ---------------------------------------------------------------------------
# config path
# ---------------------------------------------------------------------------

class TestConfigPath:
    def test_exits_0(self, tmp_path):
        r = _cli("config", "path", env=_home_env(tmp_path))
        assert r.returncode == 0

    def test_prints_audiobookmaker(self, tmp_path):
        r = _cli("config", "path", env=_home_env(tmp_path))
        assert ".audiobookmaker" in r.stdout

    def test_json_mode(self, tmp_path):
        r = _cli("config", "path", "--json", env=_home_env(tmp_path))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "path" in data
        assert ".audiobookmaker" in data["path"]


# ---------------------------------------------------------------------------
# config show
# ---------------------------------------------------------------------------

class TestConfigShow:
    def test_show_all_exits_0(self, tmp_path):
        r = _cli("config", "show", env=_home_env(tmp_path))
        assert r.returncode == 0

    def test_show_all_prints_every_field(self, tmp_path):
        r = _cli("config", "show", env=_home_env(tmp_path))
        assert r.returncode == 0
        # All UserConfig fields must appear in output
        for field in (
            "engine_id", "language", "voice_id", "speed",
            "reference_audio", "voice_description", "input_mode",
            "output_mode", "log_panel_visible", "ui_language",
        ):
            assert field in r.stdout

    def test_show_known_field_exits_0(self, tmp_path):
        r = _cli("config", "show", "engine_id", env=_home_env(tmp_path))
        assert r.returncode == 0

    def test_show_known_field_prints_value(self, tmp_path):
        r = _cli("config", "show", "engine_id", env=_home_env(tmp_path))
        # Default is 'edge'
        assert "edge" in r.stdout.strip()

    def test_show_unknown_field_exits_1(self, tmp_path):
        r = _cli("config", "show", "unknown_field", env=_home_env(tmp_path))
        assert r.returncode == 1

    def test_show_all_quiet_emits_shell_safe_lines(self, tmp_path):
        """Quiet mode is meant for shell scripts. Each line must be a
        single shell-safe 'key=value' so a future field value that
        contains '=' or whitespace cannot break line-based parsers."""
        r = _cli("config", "show", "--quiet", env=_home_env(tmp_path))
        assert r.returncode == 0
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        assert lines, "expected one line per UserConfig field"
        # Every line must have exactly one '=' on the LHS at minimum
        # (values themselves can carry '=' once shlex.quote wraps them).
        for line in lines:
            assert "=" in line, f"missing key=value separator: {line!r}"
            key, sep, _ = line.partition("=")
            assert sep == "="
            assert key.isidentifier(), f"non-identifier key on left: {key!r}"

    def test_show_all_json_is_parseable(self, tmp_path):
        r = _cli("config", "show", "--json", env=_home_env(tmp_path))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert isinstance(data, dict)
        assert "engine_id" in data

    def test_show_all_json_has_every_field(self, tmp_path):
        r = _cli("config", "show", "--json", env=_home_env(tmp_path))
        data = json.loads(r.stdout)
        for field in (
            "engine_id", "language", "voice_id", "speed",
            "reference_audio", "voice_description", "input_mode",
            "output_mode", "log_panel_visible", "ui_language",
        ):
            assert field in data

    def test_show_field_json(self, tmp_path):
        r = _cli("config", "show", "engine_id", "--json", env=_home_env(tmp_path))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "engine_id" in data


# ---------------------------------------------------------------------------
# config set
# ---------------------------------------------------------------------------

class TestConfigSet:
    def test_set_known_field_exits_0(self, tmp_path):
        r = _cli("config", "set", "engine_id", "piper", env=_home_env(tmp_path))
        assert r.returncode == 0

    def test_set_unknown_field_exits_1(self, tmp_path):
        r = _cli("config", "set", "unknown_field", "foo", env=_home_env(tmp_path))
        assert r.returncode == 1

    def test_set_roundtrip(self, tmp_path):
        env = _home_env(tmp_path)
        set_r = _cli("config", "set", "engine_id", "piper", env=env)
        assert set_r.returncode == 0
        show_r = _cli("config", "show", "engine_id", env=env)
        assert show_r.returncode == 0
        assert "piper" in show_r.stdout.strip()

    def test_set_bool_false(self, tmp_path):
        env = _home_env(tmp_path)
        r = _cli("config", "set", "log_panel_visible", "false", env=env)
        assert r.returncode == 0
        show_r = _cli("config", "show", "log_panel_visible", env=env)
        assert "False" in show_r.stdout

    def test_set_bool_true_variants(self, tmp_path):
        env = _home_env(tmp_path)
        for val in ("true", "1", "yes", "True", "YES"):
            r = _cli("config", "set", "log_panel_visible", val, env=env)
            assert r.returncode == 0, f"Failed for value {val!r}"

    def test_set_bool_false_variants(self, tmp_path):
        env = _home_env(tmp_path)
        for val in ("false", "0", "no", "False", "NO"):
            r = _cli("config", "set", "log_panel_visible", val, env=env)
            assert r.returncode == 0, f"Failed for value {val!r}"

    def test_set_bad_bool_exits_1(self, tmp_path):
        r = _cli("config", "set", "log_panel_visible", "maybe", env=_home_env(tmp_path))
        assert r.returncode == 1

    def test_set_quiet_suppresses_output(self, tmp_path):
        r = _cli("config", "set", "engine_id", "piper", "--quiet", env=_home_env(tmp_path))
        assert r.returncode == 0
        assert r.stdout.strip() == ""


# ---------------------------------------------------------------------------
# config reset
# ---------------------------------------------------------------------------

class TestConfigReset:
    def test_reset_all_exits_0(self, tmp_path):
        r = _cli("config", "reset", env=_home_env(tmp_path))
        assert r.returncode == 0

    def test_reset_after_set_restores_default(self, tmp_path):
        env = _home_env(tmp_path)
        _cli("config", "set", "engine_id", "piper", env=env)
        _cli("config", "reset", env=env)
        show_r = _cli("config", "show", "engine_id", env=env)
        assert "edge" in show_r.stdout.strip()

    def test_reset_single_field_exits_0(self, tmp_path):
        env = _home_env(tmp_path)
        r = _cli("config", "reset", "engine_id", env=env)
        assert r.returncode == 0

    def test_reset_single_field_restores_default(self, tmp_path):
        env = _home_env(tmp_path)
        _cli("config", "set", "engine_id", "piper", env=env)
        _cli("config", "reset", "engine_id", env=env)
        show_r = _cli("config", "show", "engine_id", env=env)
        assert "edge" in show_r.stdout.strip()

    def test_reset_unknown_field_exits_1(self, tmp_path):
        r = _cli("config", "reset", "unknown_field", env=_home_env(tmp_path))
        assert r.returncode == 1

    def test_reset_all_quiet(self, tmp_path):
        r = _cli("config", "reset", "--quiet", env=_home_env(tmp_path))
        assert r.returncode == 0
        assert r.stdout.strip() == ""
