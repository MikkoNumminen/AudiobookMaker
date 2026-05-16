"""Tests for the --overwrite flag on the convert and sample subcommands.

All synthesis is mocked — no audio is produced.  Tests are driven
in-process (calling _run_inner / run directly) so mock.patch can
intercept at the right layer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from src.cli import convert as convert_mod
from src.cli import sample as sample_mod
from src.cli._common import EXIT_OK


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _args(**kwargs: Any) -> argparse.Namespace:
    """Build a minimal Namespace for CLI functions."""
    defaults = {
        "json": False,
        "quiet": False,
        "dry_run": False,
        "input_format": None,
        "engine": "edge",
        "language": "fi",
        "voice": None,
        "output": None,
        "ref_audio": None,
        "voice_pack": None,
        "chunk_chars": None,
        "overwrite": "replace",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_input_txt(tmp_path: Path, content: str = "Hello world. Test sentence.") -> Path:
    """Write a minimal TXT file for use as CLI input."""
    p = tmp_path / "book.txt"
    p.write_text(content, encoding="utf-8")
    return p


# Minimal mock for an in-process engine.
def _mock_engine(available: bool = True, uses_subprocess: bool = False):
    engine = mock.MagicMock()
    engine.uses_subprocess = uses_subprocess
    status = mock.MagicMock()
    status.available = available
    status.reason = ""
    engine.check_status.return_value = status
    return engine


def _patch_inprocess_synth(output_path: str, *, write_bytes: bytes = b"mp3data"):
    """Return a context-manager that patches run_inprocess_synthesis to write
    ``write_bytes`` to ``output_path`` and call the on_event callback once
    with a 'done' event."""
    from src.launcher_bridge import ProgressEvent

    def _fake_synth(request, *, on_event):
        Path(request.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(request.output_path).write_bytes(write_bytes)
        ev = ProgressEvent(kind="done", output_path=request.output_path)
        on_event(ev)

    return mock.patch(
        "src.synthesis_orchestrator.run_inprocess_synthesis",
        side_effect=_fake_synth,
    )


def _common_patches(engine, output_path: str):
    """Return a list of context managers for the standard engine-lookup chain."""
    return [
        mock.patch("src.app_config.load", return_value=mock.MagicMock(
            engine_id="edge", language="fi", voice_id="", output_mode="single",
        )),
        mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")),
        mock.patch("src.engine_registry"),
        mock.patch("src.tts_base.get_engine", return_value=engine),
        mock.patch(
            "src.synthesis_orchestrator.suggest_output_path",
            return_value=output_path,
        ),
    ]


# ---------------------------------------------------------------------------
# 1. --overwrite replace with existing output → output is overwritten
# ---------------------------------------------------------------------------


class TestOverwriteReplace:
    def test_replace_overwrites_existing_output(self, tmp_path):
        input_file = _make_input_txt(tmp_path)
        output_path = str(tmp_path / "book.mp3")
        # Pre-create the output with old content.
        Path(output_path).write_bytes(b"old")

        engine = _mock_engine()
        patches = _common_patches(engine, output_path)

        with mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")), \
             mock.patch("src.app_config.load", return_value=mock.MagicMock(
                 engine_id="edge", language="fi", voice_id="", output_mode="single",
             )), \
             mock.patch("src.engine_registry"), \
             mock.patch("src.tts_base.get_engine", return_value=engine), \
             mock.patch(
                 "src.synthesis_orchestrator.suggest_output_path",
                 return_value=output_path,
             ), \
             _patch_inprocess_synth(output_path, write_bytes=b"new_mp3_data"):
            args = _args(input=str(input_file), overwrite="replace")
            rc = convert_mod.run(args)

        assert rc == EXIT_OK
        assert Path(output_path).read_bytes() == b"new_mp3_data"

    def test_default_behaves_like_replace(self, tmp_path):
        """No --overwrite flag (default) must behave identically to replace."""
        input_file = _make_input_txt(tmp_path)
        output_path = str(tmp_path / "book.mp3")
        Path(output_path).write_bytes(b"old")

        engine = _mock_engine()

        with mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")), \
             mock.patch("src.app_config.load", return_value=mock.MagicMock(
                 engine_id="edge", language="fi", voice_id="", output_mode="single",
             )), \
             mock.patch("src.engine_registry"), \
             mock.patch("src.tts_base.get_engine", return_value=engine), \
             mock.patch(
                 "src.synthesis_orchestrator.suggest_output_path",
                 return_value=output_path,
             ), \
             _patch_inprocess_synth(output_path, write_bytes=b"new_data"):
            # Omit overwrite from args — the default kicks in.
            ns = argparse.Namespace(
                json=False, quiet=False, dry_run=False, input_format=None,
                engine="edge", language="fi", voice=None, output=None,
                ref_audio=None, voice_pack=None, chunk_chars=None,
                input=str(input_file),
                # No 'overwrite' attribute at all — simulates argparse default.
            )
            rc = convert_mod.run(ns)

        assert rc == EXIT_OK
        assert Path(output_path).read_bytes() == b"new_data"


# ---------------------------------------------------------------------------
# 2. --overwrite skip with existing output → exit 0, synth NOT called
# ---------------------------------------------------------------------------


class TestOverwriteSkip:
    def test_skip_exits_0_when_output_exists(self, tmp_path, capsys):
        input_file = _make_input_txt(tmp_path)
        output_path = str(tmp_path / "book.mp3")
        Path(output_path).write_bytes(b"existing")

        engine = _mock_engine()
        synth_mock = mock.MagicMock()

        with mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")), \
             mock.patch("src.app_config.load", return_value=mock.MagicMock(
                 engine_id="edge", language="fi", voice_id="", output_mode="single",
             )), \
             mock.patch("src.engine_registry"), \
             mock.patch("src.tts_base.get_engine", return_value=engine), \
             mock.patch(
                 "src.synthesis_orchestrator.suggest_output_path",
                 return_value=output_path,
             ), \
             mock.patch(
                 "src.synthesis_orchestrator.run_inprocess_synthesis",
                 side_effect=synth_mock,
             ):
            args = _args(input=str(input_file), overwrite="skip")
            rc = convert_mod.run(args)

        assert rc == EXIT_OK
        synth_mock.assert_not_called()

    def test_skip_leaves_output_unchanged(self, tmp_path):
        input_file = _make_input_txt(tmp_path)
        output_path = str(tmp_path / "book.mp3")
        original = b"original_content"
        Path(output_path).write_bytes(original)

        engine = _mock_engine()

        with mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")), \
             mock.patch("src.app_config.load", return_value=mock.MagicMock(
                 engine_id="edge", language="fi", voice_id="", output_mode="single",
             )), \
             mock.patch("src.engine_registry"), \
             mock.patch("src.tts_base.get_engine", return_value=engine), \
             mock.patch(
                 "src.synthesis_orchestrator.suggest_output_path",
                 return_value=output_path,
             ), \
             mock.patch("src.synthesis_orchestrator.run_inprocess_synthesis") as synth_mock:
            args = _args(input=str(input_file), overwrite="skip")
            convert_mod.run(args)

        assert Path(output_path).read_bytes() == original
        synth_mock.assert_not_called()

    def test_skip_when_no_output_runs_synth(self, tmp_path):
        """skip must proceed normally when the output file does NOT exist yet."""
        input_file = _make_input_txt(tmp_path)
        output_path = str(tmp_path / "book.mp3")
        # Do NOT pre-create the output file.

        engine = _mock_engine()

        with mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")), \
             mock.patch("src.app_config.load", return_value=mock.MagicMock(
                 engine_id="edge", language="fi", voice_id="", output_mode="single",
             )), \
             mock.patch("src.engine_registry"), \
             mock.patch("src.tts_base.get_engine", return_value=engine), \
             mock.patch(
                 "src.synthesis_orchestrator.suggest_output_path",
                 return_value=output_path,
             ), \
             _patch_inprocess_synth(output_path, write_bytes=b"fresh"):
            args = _args(input=str(input_file), overwrite="skip")
            rc = convert_mod.run(args)

        assert rc == EXIT_OK
        assert Path(output_path).read_bytes() == b"fresh"


# ---------------------------------------------------------------------------
# 3. --overwrite skip --json → JSON event with kind: "skipped"
# ---------------------------------------------------------------------------


class TestOverwriteSkipJson:
    def test_skip_json_event(self, tmp_path, capsys):
        input_file = _make_input_txt(tmp_path)
        output_path = str(tmp_path / "book.mp3")
        Path(output_path).write_bytes(b"existing")

        engine = _mock_engine()

        with mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")), \
             mock.patch("src.app_config.load", return_value=mock.MagicMock(
                 engine_id="edge", language="fi", voice_id="", output_mode="single",
             )), \
             mock.patch("src.engine_registry"), \
             mock.patch("src.tts_base.get_engine", return_value=engine), \
             mock.patch(
                 "src.synthesis_orchestrator.suggest_output_path",
                 return_value=output_path,
             ), \
             mock.patch("src.synthesis_orchestrator.run_inprocess_synthesis"):
            args = _args(input=str(input_file), overwrite="skip", json=True)
            rc = convert_mod.run(args)

        assert rc == EXIT_OK
        captured = capsys.readouterr()
        obj = json.loads(captured.out.strip())
        assert obj["kind"] == "skipped"
        assert obj["output_path"] == output_path

    def test_skip_quiet_prints_path(self, tmp_path, capsys):
        input_file = _make_input_txt(tmp_path)
        output_path = str(tmp_path / "book.mp3")
        Path(output_path).write_bytes(b"existing")

        engine = _mock_engine()

        with mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")), \
             mock.patch("src.app_config.load", return_value=mock.MagicMock(
                 engine_id="edge", language="fi", voice_id="", output_mode="single",
             )), \
             mock.patch("src.engine_registry"), \
             mock.patch("src.tts_base.get_engine", return_value=engine), \
             mock.patch(
                 "src.synthesis_orchestrator.suggest_output_path",
                 return_value=output_path,
             ), \
             mock.patch("src.synthesis_orchestrator.run_inprocess_synthesis"):
            args = _args(input=str(input_file), overwrite="skip", quiet=True)
            rc = convert_mod.run(args)

        assert rc == EXIT_OK
        out = capsys.readouterr().out.strip()
        assert out == output_path


# ---------------------------------------------------------------------------
# 4. --overwrite fresh → cache directory deleted before synth starts
# ---------------------------------------------------------------------------


class TestOverwriteFresh:
    def test_fresh_deletes_cache_dir(self, tmp_path):
        input_file = _make_input_txt(tmp_path)
        output_path = str(tmp_path / "book.mp3")

        # Create a chunk cache directory next to the output file.
        cache_dir = tmp_path / ".chunks"
        cache_dir.mkdir()
        (cache_dir / "chunk_001.wav").write_bytes(b"cached_audio")

        engine = _mock_engine()

        # Track whether the cache still existed when synth was called.
        cache_existed_during_synth: list[bool] = []

        def _fake_synth(request, *, on_event):
            cache_existed_during_synth.append(cache_dir.exists())
            from src.launcher_bridge import ProgressEvent
            Path(request.output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(request.output_path).write_bytes(b"fresh_output")
            on_event(ProgressEvent(kind="done", output_path=request.output_path))

        with mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")), \
             mock.patch("src.app_config.load", return_value=mock.MagicMock(
                 engine_id="edge", language="fi", voice_id="", output_mode="single",
             )), \
             mock.patch("src.engine_registry"), \
             mock.patch("src.tts_base.get_engine", return_value=engine), \
             mock.patch(
                 "src.synthesis_orchestrator.suggest_output_path",
                 return_value=output_path,
             ), \
             mock.patch(
                 "src.synthesis_orchestrator.run_inprocess_synthesis",
                 side_effect=_fake_synth,
             ):
            args = _args(input=str(input_file), overwrite="fresh")
            rc = convert_mod.run(args)

        assert rc == EXIT_OK
        # Cache must have been absent when synthesis ran.
        assert cache_existed_during_synth == [False]
        # Cache directory itself must be gone.
        assert not cache_dir.exists()

    def test_fresh_no_cache_is_noop(self, tmp_path):
        """fresh with no pre-existing cache must still complete successfully."""
        input_file = _make_input_txt(tmp_path)
        output_path = str(tmp_path / "book.mp3")
        # No cache directory created.

        engine = _mock_engine()

        with mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")), \
             mock.patch("src.app_config.load", return_value=mock.MagicMock(
                 engine_id="edge", language="fi", voice_id="", output_mode="single",
             )), \
             mock.patch("src.engine_registry"), \
             mock.patch("src.tts_base.get_engine", return_value=engine), \
             mock.patch(
                 "src.synthesis_orchestrator.suggest_output_path",
                 return_value=output_path,
             ), \
             _patch_inprocess_synth(output_path, write_bytes=b"output"):
            args = _args(input=str(input_file), overwrite="fresh")
            rc = convert_mod.run(args)

        assert rc == EXIT_OK


# ---------------------------------------------------------------------------
# 5. --overwrite invalid_choice → exit 2 (argparse rejects)
# ---------------------------------------------------------------------------


class TestOverwriteInvalidChoice:
    def test_invalid_choice_exits_2(self):
        """argparse must reject an unknown --overwrite value with exit code 2."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "src.cli", "convert", "dummy.txt",
             "--overwrite", "invalid_choice"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 2

    def test_invalid_choice_stderr_mentions_choices(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "src.cli", "convert", "dummy.txt",
             "--overwrite", "oops"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 2
        assert "invalid choice" in result.stderr or "invalid_choice" in result.stderr \
            or "oops" in result.stderr


# ---------------------------------------------------------------------------
# 6. Default (no --overwrite flag) → same as replace
# ---------------------------------------------------------------------------


class TestOverwriteDefault:
    def test_no_flag_behaves_like_replace(self, tmp_path):
        """When --overwrite is absent the namespace has no 'overwrite' attr;
        _run_inner must fall back to 'replace' gracefully."""
        input_file = _make_input_txt(tmp_path)
        output_path = str(tmp_path / "book.mp3")
        Path(output_path).write_bytes(b"old")

        engine = _mock_engine()

        with mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")), \
             mock.patch("src.app_config.load", return_value=mock.MagicMock(
                 engine_id="edge", language="fi", voice_id="", output_mode="single",
             )), \
             mock.patch("src.engine_registry"), \
             mock.patch("src.tts_base.get_engine", return_value=engine), \
             mock.patch(
                 "src.synthesis_orchestrator.suggest_output_path",
                 return_value=output_path,
             ), \
             _patch_inprocess_synth(output_path, write_bytes=b"replaced"):
            # Namespace with no 'overwrite' attribute at all.
            ns = argparse.Namespace(
                json=False, quiet=False, dry_run=False, input_format=None,
                engine="edge", language="fi", voice=None, output=None,
                ref_audio=None, voice_pack=None, chunk_chars=None,
                input=str(input_file),
            )
            rc = convert_mod.run(ns)

        assert rc == EXIT_OK
        assert Path(output_path).read_bytes() == b"replaced"


# ---------------------------------------------------------------------------
# 7. sample inherits --overwrite skip → exit 0, no synth
# ---------------------------------------------------------------------------


class TestSampleInheritsOverwrite:
    def test_sample_skip_exits_0_when_output_exists(self, tmp_path):
        input_file = _make_input_txt(tmp_path)
        # The sample output path ends with _sample.mp3; we patch
        # compute_sample_output_path to return a predictable path.
        sample_output = str(tmp_path / "book_sample.mp3")
        base_output = str(tmp_path / "book.mp3")
        Path(sample_output).write_bytes(b"existing_sample")

        engine = _mock_engine()
        synth_called = mock.MagicMock()

        with mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")), \
             mock.patch("src.cli.sample.validate_input_path", return_value=(EXIT_OK, "")), \
             mock.patch("src.app_config.load", return_value=mock.MagicMock(
                 engine_id="edge", language="fi", voice_id="", output_mode="single",
             )), \
             mock.patch("src.engine_registry"), \
             mock.patch("src.tts_base.get_engine", return_value=engine), \
             mock.patch(
                 "src.synthesis_orchestrator.suggest_output_path",
                 return_value=base_output,
             ), \
             mock.patch(
                 "src.sample_helpers.compute_sample_output_path",
                 return_value=sample_output,
             ), \
             mock.patch("src.synthesis_orchestrator.parse_book") as mock_parse, \
             mock.patch(
                 "src.synthesis_orchestrator.run_inprocess_synthesis",
                 side_effect=synth_called,
             ):
            mock_parse.return_value = mock.MagicMock(full_text="Hello world.")
            with mock.patch(
                "src.sample_helpers.extract_sample_text",
                return_value="Hello world.",
            ):
                args = _args(input=str(input_file), overwrite="skip")
                rc = sample_mod.run(args)

        assert rc == EXIT_OK
        synth_called.assert_not_called()

    def test_sample_parser_has_overwrite_flag(self):
        """The sample subparser must expose --overwrite in its help text."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "src.cli", "sample", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 0
        assert "--overwrite" in result.stdout

    def test_convert_parser_has_overwrite_flag(self):
        """The convert subparser must expose --overwrite in its help text."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "src.cli", "convert", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 0
        assert "--overwrite" in result.stdout
