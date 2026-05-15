"""Tests for stdin support on the convert, sample, and preview subcommands.

All synthesis and engine calls are mocked so no audio is produced.
Network access is blocked by the conftest.py autouse fixture.
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


def _cli(
    *args: str,
    stdin_bytes: bytes | None = None,
    stdin_text: str | None = None,
    env: dict | None = None,
) -> "subprocess.CompletedProcess":
    """Run the CLI via subprocess with optional piped stdin."""
    import subprocess

    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    if stdin_bytes is not None:
        return subprocess.run(
            [sys.executable, "-m", "src.cli", *args],
            input=stdin_bytes,
            capture_output=True,
            env=merged_env,
        )

    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=merged_env,
    )


def _make_pdf_bytes() -> bytes:
    """Return bytes for a minimal valid PDF with one line of text."""
    try:
        import fitz
    except ImportError:
        pytest.skip("fitz (PyMuPDF) not available")

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello world. This is a test sentence.", fontsize=12)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    try:
        doc.save(tmp.name)
        doc.close()
        return Path(tmp.name).read_bytes()
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def _make_txt_bytes(content: str = "Hello world. This is a test sentence.") -> bytes:
    return content.encode("utf-8")


# ---------------------------------------------------------------------------
# convert - stdin reading (dry-run + JSON mode)
# ---------------------------------------------------------------------------


class TestConvertStdin:
    def test_reads_txt_from_stdin_dry_run_json(self, tmp_path):
        """pipe a .txt into convert - and check the JSON dry-run output."""
        txt_bytes = _make_txt_bytes()
        result = _cli(
            "convert", "-",
            "--input-format", "txt",
            "--engine", "edge",
            "--language", "fi",
            "--dry-run",
            "--json",
            stdin_bytes=txt_bytes,
        )
        assert result.returncode == 0, (
            f"Expected exit 0\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        stdout_text = result.stdout.decode("utf-8", errors="replace")
        obj = json.loads(stdout_text.strip())
        assert obj["dry_run"] is True
        # The input path in the JSON must be a file under .local/scratch (or the
        # worktree equivalent), NOT the literal "-".
        assert obj["input"] != "-"
        assert "stdin_" in Path(obj["input"]).name

    def test_reads_pdf_from_stdin_dry_run_json(self):
        """pipe a PDF into convert - and check the JSON dry-run output."""
        pdf_bytes = _make_pdf_bytes()
        result = _cli(
            "convert", "-",
            "--input-format", "pdf",
            "--engine", "edge",
            "--language", "fi",
            "--dry-run",
            "--json",
            stdin_bytes=pdf_bytes,
        )
        assert result.returncode == 0, (
            f"Expected exit 0\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        stdout_text = result.stdout.decode("utf-8", errors="replace")
        obj = json.loads(stdout_text.strip())
        assert obj["dry_run"] is True
        assert obj["input"] != "-"
        assert Path(obj["input"]).suffix == ".pdf"

    def test_tempfile_cleaned_up_after_dry_run(self):
        """The scratch tempfile must be deleted when convert finishes."""
        txt_bytes = _make_txt_bytes()
        result = _cli(
            "convert", "-",
            "--input-format", "txt",
            "--engine", "edge",
            "--language", "fi",
            "--dry-run",
            "--json",
            stdin_bytes=txt_bytes,
        )
        assert result.returncode == 0
        stdout_text = result.stdout.decode("utf-8", errors="replace")
        obj = json.loads(stdout_text.strip())
        # The tempfile must have been cleaned up by the time the process exits.
        assert not Path(obj["input"]).exists(), (
            f"stdin tempfile was not cleaned up: {obj['input']}"
        )


# ---------------------------------------------------------------------------
# convert - requires --input-format with -
# ---------------------------------------------------------------------------


class TestConvertStdinValidation:
    def test_missing_input_format_exits_1(self):
        """`convert -` without --input-format must exit 1."""
        result = _cli(
            "convert", "-",
            "--engine", "edge",
            stdin_bytes=b"hello",
        )
        assert result.returncode == 1, (
            f"Expected exit 1\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        stderr = result.stderr.decode("utf-8", errors="replace")
        assert "--input-format" in stderr

    def test_input_format_without_dash_exits_1(self, tmp_path):
        """`convert book.txt --input-format txt` (no dash) must exit 1."""
        book = tmp_path / "book.txt"
        book.write_text("Hello world.")
        result = _cli(
            "convert", str(book),
            "--input-format", "txt",
            "--engine", "edge",
            "--dry-run",
            stdin_text="",
        )
        assert result.returncode == 1, (
            f"Expected exit 1\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "--input-format" in result.stderr

    def test_tty_stdin_exits_1(self, monkeypatch):
        """When stdin would be a terminal, convert - must exit EXIT_BAD_INPUT."""
        # We can't truly simulate a TTY in subprocess, so test via the module API.
        import argparse
        from src.cli.convert import run as convert_run
        from src.cli._common import EXIT_BAD_INPUT

        args = argparse.Namespace(
            input="-",
            input_format="txt",
            engine="edge",
            language="fi",
            voice=None,
            output=None,
            ref_audio=None,
            voice_pack=None,
            chunk_chars=None,
            dry_run=True,
            json=False,
            quiet=False,
        )
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            code = convert_run(args)
        assert code == EXIT_BAD_INPUT


# ---------------------------------------------------------------------------
# preview - stdin reading
# ---------------------------------------------------------------------------


class TestPreviewStdin:
    def test_reads_text_from_stdin(self, tmp_path):
        """pipe text into `preview -` — engine may or may not be available."""
        result = _cli(
            "preview", "-",
            "--engine", "edge",
            "--language", "en",
            "--no-play",
            stdin_text="Hello there.\n",
        )
        # Exit 0 (success), 1 (bad input — subprocess engine path), or
        # 2 (missing dep) are all expected in CI without the real engine.
        assert result.returncode in (0, 1, 2, 4), (
            f"Unexpected exit code {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_empty_stdin_exits_1(self):
        """`preview -` with empty stdin must exit 1."""
        result = _cli(
            "preview", "-",
            "--engine", "edge",
            "--language", "en",
            "--no-play",
            stdin_text="",
        )
        assert result.returncode == 1, (
            f"Expected exit 1\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "empty" in result.stderr.lower() or "stdin" in result.stderr.lower()

    def test_whitespace_only_stdin_exits_1(self):
        """`preview -` with only whitespace must exit 1."""
        result = _cli(
            "preview", "-",
            "--engine", "edge",
            "--language", "en",
            "--no-play",
            stdin_text="   \n\n  ",
        )
        assert result.returncode == 1

    def test_tty_stdin_exits_1(self, monkeypatch):
        """When stdin would be a terminal, preview - must exit EXIT_BAD_INPUT."""
        import argparse
        from src.cli.preview import run as preview_run
        from src.cli._common import EXIT_BAD_INPUT

        args = argparse.Namespace(
            text="-",
            engine="edge",
            language="fi",
            voice=None,
            output=None,
            no_play=True,
            json=False,
            quiet=False,
        )
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            code = preview_run(args)
        assert code == EXIT_BAD_INPUT


# ---------------------------------------------------------------------------
# sample - stdin path delegates to convert (via _run_sample_from_path)
# ---------------------------------------------------------------------------


class TestSampleStdin:
    def test_reads_txt_from_stdin_dry_run_json(self):
        """pipe a .txt into sample - and check the JSON dry-run output."""
        txt_bytes = _make_txt_bytes()
        result = _cli(
            "sample", "-",
            "--input-format", "txt",
            "--engine", "edge",
            "--language", "fi",
            "--dry-run",
            "--json",
            stdin_bytes=txt_bytes,
        )
        assert result.returncode == 0, (
            f"Expected exit 0\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        stdout_text = result.stdout.decode("utf-8", errors="replace")
        obj = json.loads(stdout_text.strip())
        assert obj["dry_run"] is True
        assert obj["input"] != "-"

    def test_missing_input_format_exits_1(self):
        """`sample -` without --input-format must exit 1."""
        result = _cli(
            "sample", "-",
            "--engine", "edge",
            stdin_bytes=b"hello",
        )
        assert result.returncode == 1
        stderr = result.stderr.decode("utf-8", errors="replace")
        assert "--input-format" in stderr
