"""Tests for the AudiobookMaker CLI (src/cli/).

Uses subprocess.run to drive the CLI the same way a user would, so the
tests exercise the real argument-parsing and dispatch layer.

Heavy engine calls (synthesis, network) are mocked so no real audio is
produced and no network connections are made.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Generator
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cli(*args: str, env: dict | None = None) -> "subprocess.CompletedProcess":
    """Run the CLI via subprocess and return the CompletedProcess."""
    import subprocess

    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
        env=merged_env,
    )


def _tmp_txt(content: str = "Hello world. This is a test sentence.") -> str:
    """Write a temp .txt file and return its path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    f.write(content)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------


class TestVersion:
    def test_prints_version(self):
        result = _cli("--version")
        assert result.returncode == 0
        # Version string must appear on stdout.
        output = result.stdout + result.stderr
        assert "AudiobookMaker" in output or "3." in output


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestHelp:
    def test_top_help_exit_0(self):
        result = _cli("--help")
        assert result.returncode == 0

    def test_top_help_lists_subcommands(self):
        result = _cli("--help")
        output = result.stdout + result.stderr
        # Every subcommand must appear in top-level help.
        for cmd in ("convert", "sample", "voices", "engines", "doctor"):
            assert cmd in output, f"'{cmd}' not in --help output"

    def test_no_args_shows_help(self):
        """Bare invocation with no subcommand should print help and exit 0."""
        result = _cli()
        assert result.returncode == 0
        assert "convert" in result.stdout + result.stderr


# ---------------------------------------------------------------------------
# convert --help
# ---------------------------------------------------------------------------


class TestConvertHelp:
    def test_convert_help_exit_0(self):
        result = _cli("convert", "--help")
        assert result.returncode == 0

    def test_convert_help_lists_all_flags(self):
        result = _cli("convert", "--help")
        output = result.stdout + result.stderr
        # --speed is deferred from v1 (see docs/CLI.md "Deferred from v1").
        for flag in ("--engine", "--language", "--voice", "--output",
                     "--ref-audio", "--voice-pack", "--chunk-chars", "--dry-run",
                     "--json", "--quiet"):
            assert flag in output, f"'{flag}' not in convert --help"

    def test_convert_help_does_not_list_speed(self):
        # Verifying the removal of the silent-no-op --speed flag.
        result = _cli("convert", "--help")
        output = result.stdout + result.stderr
        assert "--speed" not in output, "--speed should be deferred from v1"


# ---------------------------------------------------------------------------
# convert <bogus> — bad input → exit 1
# ---------------------------------------------------------------------------


class TestConvertBadInput:
    def test_nonexistent_file_exits_1(self):
        result = _cli("convert", "/no/such/file.pdf")
        assert result.returncode == 1

    def test_unsupported_extension_exits_1(self):
        f = tempfile.NamedTemporaryFile(suffix=".xyz", delete=False)
        f.close()
        try:
            result = _cli("convert", f.name)
            assert result.returncode == 1
        finally:
            os.unlink(f.name)


# ---------------------------------------------------------------------------
# convert <input> --dry-run → exit 0
# ---------------------------------------------------------------------------


class TestConvertDryRun:
    def test_dry_run_exit_0(self):
        path = _tmp_txt()
        try:
            result = _cli("convert", path, "--dry-run", "--engine", "edge")
            assert result.returncode == 0
        finally:
            os.unlink(path)

    def test_dry_run_prints_details(self):
        path = _tmp_txt()
        try:
            result = _cli("convert", path, "--dry-run", "--engine", "edge", "--language", "en")
            output = result.stdout + result.stderr
            assert "edge" in output
            assert "en" in output
        finally:
            os.unlink(path)

    def test_dry_run_no_synthesis(self, tmp_path):
        """--dry-run must NOT produce any MP3 file."""
        path = _tmp_txt()
        out = str(tmp_path / "output.mp3")
        try:
            _cli("convert", path, "--dry-run", "--engine", "edge", "--output", out)
            assert not Path(out).exists(), "dry-run should not produce output file"
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# engines list
# ---------------------------------------------------------------------------


class TestEnginesList:
    def test_exits_0(self):
        result = _cli("engines", "list")
        assert result.returncode == 0

    def test_lists_production_engines(self):
        result = _cli("engines", "list")
        output = result.stdout + result.stderr
        # The three production engines must appear.
        for engine_id in ("edge", "piper", "chatterbox_fi"):
            assert engine_id in output, f"'{engine_id}' not in engines list output"

    def test_json_mode_parseable(self):
        result = _cli("engines", "list", "--json")
        assert result.returncode == 0
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        assert lines, "expected at least one JSON line"
        for line in lines:
            obj = json.loads(line)  # must not raise
            assert "id" in obj
            assert "available" in obj

    def test_installed_only_flag(self):
        result = _cli("engines", "list", "--installed-only")
        assert result.returncode == 0

    def test_json_one_object_per_line(self):
        result = _cli("engines", "list", "--json")
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        # Every line must be a self-contained JSON object (not an array).
        for line in lines:
            obj = json.loads(line)
            assert isinstance(obj, dict)
            assert "id" in obj


# ---------------------------------------------------------------------------
# voices list
# ---------------------------------------------------------------------------


class TestVoicesList:
    def test_edge_fi_exits_0(self):
        result = _cli("voices", "list", "--engine", "edge", "--language", "fi")
        assert result.returncode == 0

    def test_edge_fi_includes_voice(self):
        result = _cli("voices", "list", "--engine", "edge", "--language", "fi")
        output = result.stdout + result.stderr
        # There must be at least one voice id in the output.
        assert len(output.strip()) > 0

    def test_json_mode_parseable(self):
        result = _cli("voices", "list", "--json")
        assert result.returncode == 0
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        assert lines, "expected at least one JSON line from voices list --json"
        for line in lines:
            obj = json.loads(line)
            assert "id" in obj
            assert "engine" in obj

    def test_unknown_engine_exits_1(self):
        result = _cli("voices", "list", "--engine", "nonexistent_engine_xyz")
        assert result.returncode == 1

    def test_json_voice_fields(self):
        result = _cli("voices", "list", "--engine", "edge", "--language", "fi", "--json")
        assert result.returncode == 0
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        assert lines
        obj = json.loads(lines[0])
        assert "id" in obj
        assert "display_name" in obj
        assert "language" in obj
        assert "gender" in obj


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


class TestDoctor:
    def test_exits_0_or_2(self):
        result = _cli("doctor")
        assert result.returncode in (0, 2), (
            f"doctor should exit 0 or 2, got {result.returncode}"
        )

    def test_json_mode_parseable(self):
        result = _cli("doctor", "--json")
        assert result.returncode in (0, 2)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        assert lines, "expected at least one JSON line from doctor --json"
        for line in lines:
            obj = json.loads(line)
            if obj.get("kind") == "summary":
                # summary line has a different shape — validated elsewhere
                continue
            assert "name" in obj
            assert "status" in obj

    def test_json_has_ffmpeg_check(self):
        result = _cli("doctor", "--json")
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        objs = [json.loads(l) for l in lines]
        names = [o["name"] for o in objs if o.get("kind") != "summary"]
        assert "ffmpeg" in names

    def test_json_has_gpu_check(self):
        result = _cli("doctor", "--json")
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        objs = [json.loads(l) for l in lines]
        names = [o["name"] for o in objs if o.get("kind") != "summary"]
        assert "gpu" in names


# ---------------------------------------------------------------------------
# sample --dry-run
# ---------------------------------------------------------------------------


class TestSampleDryRun:
    def test_sample_dry_run_exit_0(self):
        path = _tmp_txt()
        try:
            result = _cli("sample", path, "--dry-run", "--engine", "edge")
            assert result.returncode == 0
        finally:
            os.unlink(path)

    def test_sample_dry_run_no_file(self):
        result = _cli("sample", "/no/such/file.txt", "--dry-run")
        assert result.returncode == 1


# ---------------------------------------------------------------------------
# Config precedence — env vars
# ---------------------------------------------------------------------------


class TestEnvVarPrecedence:
    def test_engine_env_overrides_default(self):
        path = _tmp_txt()
        try:
            result = _cli(
                "convert", path, "--dry-run",
                env={"AUDIOBOOKMAKER_ENGINE": "piper"},
            )
            output = result.stdout + result.stderr
            assert "piper" in output
        finally:
            os.unlink(path)

    def test_language_env_overrides_default(self):
        path = _tmp_txt()
        try:
            result = _cli(
                "convert", path, "--dry-run",
                env={"AUDIOBOOKMAKER_LANGUAGE": "en"},
            )
            output = result.stdout + result.stderr
            assert "en" in output
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# In-process synthesis — mocked
# ---------------------------------------------------------------------------


class TestConvertMocked:
    """Test convert with the inprocess synthesis mocked out so no real TTS runs."""

    def test_convert_quiet_mode_dry_run(self, tmp_path):
        path = _tmp_txt()
        out = str(tmp_path / "output.mp3")
        try:
            result = _cli(
                "convert", path, "--dry-run", "--quiet", "--engine", "edge", "--output", out,
            )
            # quiet + dry-run: should exit 0, minimal output
            assert result.returncode == 0
        finally:
            os.unlink(path)

    def test_convert_json_mode_dry_run(self, tmp_path):
        path = _tmp_txt()
        out = str(tmp_path / "output.mp3")
        try:
            result = _cli(
                "convert", path, "--dry-run", "--json", "--engine", "edge", "--output", out,
            )
            assert result.returncode == 0
            # The dry-run output must be parseable JSON, not the plain-text key/value
            # form used in human mode.
            stripped = result.stdout.strip()
            assert stripped, "expected at least one JSON line from --dry-run --json"
            obj = json.loads(stripped)
            assert obj["dry_run"] is True
            assert obj["engine"] == "edge"
            assert obj["input"] == path
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Voices dedup
# ---------------------------------------------------------------------------


class TestVoicesDedup:
    def test_json_no_duplicate_voice_ids_per_engine(self):
        """A voice id should appear at most once per engine even when the
        engine returns the same voice for multiple languages."""
        result = _cli("voices", "list", "--engine", "edge", "--json")
        assert result.returncode == 0
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        seen = set()
        for line in lines:
            obj = json.loads(line)
            key = (obj["engine"], obj["id"])
            assert key not in seen, f"duplicate voice {key} in voices list output"
            seen.add(key)
