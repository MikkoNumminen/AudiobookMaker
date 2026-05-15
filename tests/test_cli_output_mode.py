"""Tests for the --output-mode flag on convert and sample subcommands.

Tests 1, 3, 4, 5, 6 use subprocess (--dry-run) so no synthesis runs.
Test 2 (engine compatibility) uses direct import + mock because the
check fires after engine availability, which requires the engine to
report available=True — unavailable engines exit with code 2 before
the per-chapter guard is reached.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
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
    # Remove any lingering AUDIOBOOKMAKER_OUTPUT_MODE from the parent env
    # unless the test explicitly sets it.
    if env is None or "AUDIOBOOKMAKER_OUTPUT_MODE" not in env:
        merged_env.pop("AUDIOBOOKMAKER_OUTPUT_MODE", None)

    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
        env=merged_env,
    )


def _tmp_txt(content: str = "Hello world. This is a test sentence.") -> str:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    f.write(content)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# Test 1 — dry-run + per-chapter + edge → exit 0, JSON has output_mode
# ---------------------------------------------------------------------------


class TestDryRunPerChapterJson:
    def test_exit_0_and_output_mode_in_json(self):
        path = _tmp_txt()
        try:
            result = _cli(
                "convert", path,
                "--dry-run", "--json",
                "--engine", "edge",
                "--output-mode", "per-chapter",
            )
            assert result.returncode == 0, (
                f"expected exit 0, got {result.returncode}\n"
                f"stderr: {result.stderr}\nstdout: {result.stdout}"
            )
            lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
            assert lines, "expected at least one JSON line"
            obj = json.loads(lines[0])
            assert obj.get("output_mode") == "per-chapter", (
                f"output_mode not 'per-chapter' in dry-run JSON: {obj}"
            )
            assert obj.get("dry_run") is True
        finally:
            os.unlink(path)

    def test_dry_run_flag_only_json_contains_output_mode_field(self):
        """The JSON must always include output_mode even when not set."""
        path = _tmp_txt()
        try:
            result = _cli(
                "convert", path, "--dry-run", "--json", "--engine", "edge",
            )
            assert result.returncode == 0
            obj = json.loads(result.stdout.strip().splitlines()[0])
            assert "output_mode" in obj
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Test 2 — per-chapter + incompatible engine → exit 1 with helpful message
# ---------------------------------------------------------------------------


class TestEngineCompatibilityCheck:
    """Verify that per-chapter mode rejects non-supporting engines.

    Uses direct CLI import + mock so the test does not depend on piper
    being installed — an unavailable engine would fail with exit 2
    (missing dep) before we ever reach the per-chapter guard.
    """

    def _run_convert_with_mock_engine(
        self, engine_id: str, *, supports_per_chapter: bool
    ) -> tuple[int, str]:
        """Invoke convert._run_inner directly with a mocked engine."""
        import argparse
        from src.cli import convert

        # Minimal args namespace.
        args = argparse.Namespace(
            input="dummy.txt",
            engine=engine_id,
            language="fi",
            voice=None,
            output=None,
            output_mode="per-chapter",
            ref_audio=None,
            voice_pack=None,
            chunk_chars=None,
            dry_run=False,
            json=False,
            quiet=False,
            input_format=None,
        )

        # Fake engine object.
        fake_engine = mock.MagicMock()
        fake_engine.uses_subprocess = False
        fake_engine.supports_per_chapter = supports_per_chapter
        fake_engine.check_status.return_value = mock.MagicMock(
            available=True, reason=""
        )

        stderr_lines: list[str] = []

        import io
        buf = io.StringIO()
        with (
            mock.patch("src.cli.convert.validate_input_path", return_value=(0, "")),
            mock.patch("src.app_config.load", return_value=mock.MagicMock(
                engine_id=engine_id, language="fi", voice_id="",
                output_mode="single",
            )),
            mock.patch("src.synthesis_orchestrator.suggest_output_path",
                       return_value="/tmp/out.mp3"),
            mock.patch("src.engine_registry"),
            mock.patch("src.tts_base.get_engine", return_value=fake_engine),
            mock.patch("sys.stderr", buf),
        ):
            code = convert._run_inner(
                args,
                input_path="dummy.txt",
                sample_text=None,
                json_mode=False,
                quiet=False,
                dry_run=False,
                stdin_tempfile=None,
            )
            return code, buf.getvalue()

    def test_piper_per_chapter_exits_1(self):
        code, stderr = self._run_convert_with_mock_engine(
            "piper", supports_per_chapter=False
        )
        assert code == 1, f"expected exit 1, got {code}\nstderr: {stderr}"
        assert "piper" in stderr, f"engine name not in error: {stderr}"
        assert "per-chapter" in stderr or "single" in stderr, (
            f"actionable hint not in error: {stderr}"
        )

    def test_edge_per_chapter_does_not_exit_1_at_guard(self):
        """Edge supports per-chapter — the guard must not reject it."""
        # Edge supports per-chapter, so control falls through to synthesis.
        # We don't care about the synthesis result here — just that the
        # guard itself doesn't fire.  Patch synthesize to raise so we can
        # distinguish "passed the guard" from "synthesis error" vs "guard
        # rejected".
        import argparse
        from src.cli import convert
        from src.cli._common import EXIT_BAD_INPUT

        args = argparse.Namespace(
            input="dummy.txt",
            engine="edge",
            language="fi",
            voice=None,
            output=None,
            output_mode="per-chapter",
            ref_audio=None,
            voice_pack=None,
            chunk_chars=None,
            dry_run=False,
            json=False,
            quiet=False,
            input_format=None,
        )

        fake_engine = mock.MagicMock()
        fake_engine.uses_subprocess = False
        fake_engine.supports_per_chapter = True
        fake_engine.check_status.return_value = mock.MagicMock(
            available=True, reason=""
        )

        with (
            mock.patch("src.cli.convert.validate_input_path", return_value=(0, "")),
            mock.patch("src.app_config.load", return_value=mock.MagicMock(
                engine_id="edge", language="fi", voice_id="",
                output_mode="single",
            )),
            mock.patch("src.synthesis_orchestrator.suggest_output_path",
                       return_value="/tmp/out.mp3"),
            mock.patch("src.engine_registry"),
            mock.patch("src.tts_base.get_engine", return_value=fake_engine),
            mock.patch("src.cli.convert._run_inprocess", return_value=0),
        ):
            import io
            buf = io.StringIO()
            with mock.patch("sys.stderr", buf):
                code = convert._run_inner(
                    args,
                    input_path="dummy.txt",
                    sample_text=None,
                    json_mode=False,
                    quiet=False,
                    dry_run=False,
                    stdin_tempfile=None,
                )
            assert code != EXIT_BAD_INPUT, (
                "edge engine must not be rejected by the per-chapter guard"
            )


# ---------------------------------------------------------------------------
# Test 3 — invalid choice → exit 2 (argparse)
# ---------------------------------------------------------------------------


class TestInvalidOutputMode:
    def test_invalid_choice_exits_2(self):
        path = _tmp_txt()
        try:
            result = _cli(
                "convert", path,
                "--dry-run",
                "--output-mode", "invalid_value",
            )
            # argparse rejects unknown choices with exit code 2.
            assert result.returncode == 2, (
                f"expected exit 2, got {result.returncode}\n"
                f"stderr: {result.stderr}"
            )
        finally:
            os.unlink(path)

    def test_invalid_choice_on_sample_exits_2(self):
        path = _tmp_txt()
        try:
            result = _cli(
                "sample", path,
                "--dry-run",
                "--output-mode", "bad_mode",
            )
            assert result.returncode == 2
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Test 4 — default behaviour (no --output-mode) → output_mode == "single"
# ---------------------------------------------------------------------------


class TestDefaultOutputMode:
    def test_default_is_single_in_dry_run_json(self):
        """No --output-mode flag → built-in default 'single'."""
        path = _tmp_txt()
        try:
            result = _cli(
                "convert", path,
                "--dry-run", "--json",
                "--engine", "edge",
                env={"AUDIOBOOKMAKER_OUTPUT_MODE": ""},  # ensure env is absent
            )
            # Strip the empty env value so it's truly absent.
            assert result.returncode == 0
            lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
            obj = json.loads(lines[0])
            assert obj.get("output_mode") == "single", (
                f"default output_mode should be 'single', got: {obj.get('output_mode')}"
            )
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Test 5 — env-var override
# ---------------------------------------------------------------------------


class TestEnvVarOutputMode:
    def test_env_var_per_chapter_reflected_in_dry_run_json(self):
        path = _tmp_txt()
        try:
            result = _cli(
                "convert", path,
                "--dry-run", "--json",
                "--engine", "edge",
                env={"AUDIOBOOKMAKER_OUTPUT_MODE": "per-chapter"},
            )
            assert result.returncode == 0, (
                f"exit {result.returncode}\nstderr: {result.stderr}"
            )
            lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
            obj = json.loads(lines[0])
            assert obj.get("output_mode") == "per-chapter", (
                f"env var should set output_mode to 'per-chapter': {obj}"
            )
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Test 6 — flag wins over env var
# ---------------------------------------------------------------------------


class TestFlagWinsOverEnv:
    def test_flag_single_overrides_env_per_chapter(self):
        path = _tmp_txt()
        try:
            result = _cli(
                "convert", path,
                "--dry-run", "--json",
                "--engine", "edge",
                "--output-mode", "single",
                env={"AUDIOBOOKMAKER_OUTPUT_MODE": "per-chapter"},
            )
            assert result.returncode == 0
            lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
            obj = json.loads(lines[0])
            assert obj.get("output_mode") == "single", (
                f"--output-mode single should win over env var: {obj}"
            )
        finally:
            os.unlink(path)

    def test_flag_per_chapter_overrides_env_single(self):
        path = _tmp_txt()
        try:
            result = _cli(
                "convert", path,
                "--dry-run", "--json",
                "--engine", "edge",
                "--output-mode", "per-chapter",
                env={"AUDIOBOOKMAKER_OUTPUT_MODE": "single"},
            )
            assert result.returncode == 0
            lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
            obj = json.loads(lines[0])
            assert obj.get("output_mode") == "per-chapter", (
                f"--output-mode per-chapter should win over env var: {obj}"
            )
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Flag presence in --help
# ---------------------------------------------------------------------------


class TestOutputModeFlagInHelp:
    def test_convert_help_lists_output_mode(self):
        result = _cli("convert", "--help")
        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "--output-mode" in output, "--output-mode not in convert --help"

    def test_sample_help_lists_output_mode(self):
        result = _cli("sample", "--help")
        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "--output-mode" in output, "--output-mode not in sample --help"
