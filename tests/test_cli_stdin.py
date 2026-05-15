"""Tests for stdin support on the convert, sample, and preview subcommands.

All synthesis and engine calls are mocked so no audio is produced.
Network access is blocked by the conftest.py autouse fixture.
"""

from __future__ import annotations

import json
import os
import sys
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
    """Return bytes for a minimal valid PDF with one line of text.

    Uses ``Document.tobytes()`` (a.k.a. ``Document.write()`` in older
    PyMuPDF) to avoid a disk roundtrip.
    """
    try:
        import fitz
    except ImportError:
        pytest.skip("fitz (PyMuPDF) not available")

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello world. This is a test sentence.", fontsize=12)
    try:
        # PyMuPDF >=1.22 spells it tobytes(); older releases used write().
        if hasattr(doc, "tobytes"):
            data = doc.tobytes()
        else:
            data = doc.write()
    finally:
        doc.close()
    return data


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

    def test_tempfile_cleaned_up_on_synth_failure(self, monkeypatch):
        """If the synth path raises after stdin is materialized, the
        try/finally must still delete the tempfile."""
        import argparse
        from io import BytesIO

        from src.cli import convert as convert_mod
        from src.cli._common import EXIT_INTERNAL

        # Fake stdin so the materialize helper has something to read.
        class _StreamWithBuffer:
            def __init__(self, data: bytes):
                self.buffer = BytesIO(data)
            def isatty(self):
                return False

        monkeypatch.setattr(sys, "stdin", _StreamWithBuffer(b"some bytes"))

        # Capture the tempfile path that the helper produces. Spy on
        # cleanup_stdin_tempfile to confirm it's called even on failure.
        produced_paths: list[str] = []
        original_materialize = convert_mod.materialize_stdin_to_tempfile

        def _spy_materialize(fmt: str):
            path, code, msg = original_materialize(fmt)
            if path is not None:
                produced_paths.append(path)
            return path, code, msg

        monkeypatch.setattr(convert_mod, "materialize_stdin_to_tempfile", _spy_materialize)

        # Force the inner synth path to raise — the try/finally must
        # still cleanup the tempfile we just wrote.
        def _boom(*_a, **_kw):
            raise RuntimeError("synth blew up")

        monkeypatch.setattr(convert_mod, "_run_inner", _boom)

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
            dry_run=False,
            json=False,
            quiet=False,
        )

        # The raise propagates out; the finally still runs. We assert
        # the cleanup happened regardless.
        with pytest.raises(RuntimeError):
            convert_mod.run(args)

        assert produced_paths, "materialize_stdin_to_tempfile was never called"
        for p in produced_paths:
            assert not Path(p).exists(), (
                f"stdin tempfile leaked on synth failure: {p}"
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
    def test_reads_text_from_stdin_unit(self, monkeypatch):
        """Drive preview.run directly with mocked stdin and engine.

        Asserts the piped text actually reaches the engine's synthesize
        call. This replaces an earlier subprocess test that allowed any
        of (0, 1, 2, 4) as the exit code — a contract so loose it would
        have passed even if stdin parsing was broken.

        The mock targets the *source* module ``src.tts_base.get_engine``
        because preview.run() does ``from src.tts_base import get_engine``
        inside the function body — so the name resolves through
        ``tts_base`` at call time, not through ``preview`` as a module
        attribute. Likewise ``--no-play`` is set so the playback path
        (which would need ``_audio_player`` monkeypatched too) is never
        reached.
        """
        import argparse
        from io import BytesIO, TextIOWrapper

        from src.cli import preview as preview_mod
        from src.cli._common import EXIT_OK

        piped_text = "Hello from stdin pipe."

        # Wrap a BytesIO in a TextIOWrapper so sys.stdin.read() returns
        # str (matching real-world behaviour). isatty() returns False on
        # a wrapped BytesIO so the TTY guard correctly lets us through.
        fake_stdin = TextIOWrapper(BytesIO(piped_text.encode("utf-8")))
        monkeypatch.setattr(sys, "stdin", fake_stdin)

        # Capture what text was synthesized. The stub mirrors the real
        # TTSEngine.synthesize signature (text, output_path, voice_id,
        # language, progress_cb) so a future contract drift surfaces
        # here as a TypeError rather than a silent skip.
        synth_calls = []

        class _StubEngine:
            uses_subprocess = False

            def synthesize(self, text, output_path, voice_id=None,
                           language=None, progress_cb=None, **kwargs):
                synth_calls.append({"text": text, "output_path": output_path})
                # Write dummy bytes so preview's "synth produced output" path
                # is satisfied — preview doesn't validate the bytes itself.
                with open(output_path, "wb") as fh:
                    fh.write(b"\x00" * 1024)
                return output_path

            def check_status(self):
                return mock.Mock(available=True, reason="")

        # preview.py imports get_engine locally as
        # `from src.tts_base import get_engine`, so the name resolves
        # against src.tts_base at call time. Monkeypatch at that source.
        monkeypatch.setattr("src.tts_base.get_engine", lambda _id: _StubEngine())

        args = argparse.Namespace(
            text="-",
            engine="edge",
            language="en",
            voice=None,
            output=None,
            no_play=True,
            json=False,
            quiet=True,
        )
        code = preview_mod.run(args)

        # Loud failure if the mock didn't bind or the synth call wasn't
        # reached — the whole point of this test is to verify stdin text
        # flows through to the engine.
        assert synth_calls, (
            f"engine.synthesize was never reached (exit code {code}). "
            "Either the mock binding is wrong or preview.run() bailed "
            "before the synth call."
        )
        assert code == EXIT_OK, (
            f"expected EXIT_OK (0), got {code}; stub engine was called "
            f"with text={synth_calls[0]['text']!r}"
        )
        assert synth_calls[0]["text"].strip() == piped_text

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
