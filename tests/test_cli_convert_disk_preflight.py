"""Tests for disk-space preflight check in the convert subcommand.

The preflight calls check_output_disk_space(output_path, text_chars, engine_id)
which returns (has_enough: bool, free_mb: float, needed_mb: float).  It is
skipped entirely on --dry-run because no synthesis happens there.
"""

from __future__ import annotations

import argparse
import io
from unittest import mock

import pytest

from src.cli import convert
from src.cli._common import EXIT_MISSING_DEP, EXIT_OK


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _base_args(**overrides) -> argparse.Namespace:
    """Return a minimal Namespace that satisfies _run_inner's attribute reads."""
    defaults = dict(
        input="dummy.txt",
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
        input_format=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _run_inner_with_mocks(args, *, disk_result, synth_return=EXIT_OK):
    """
    Invoke convert._run_inner with the minimum mocks needed:

    - validate_input_path → OK
    - app_config.load     → minimal config
    - suggest_output_path → fixed path
    - engine_registry     → noop
    - get_engine          → fake available engine (in-process)
    - parse_book          → fake ParsedBook with non-empty full_text so
                            text_chars > 0 and the disk check is reached
    - check_output_disk_space → caller-supplied disk_result tuple
    - _run_inprocess      → synth_return (only reached when disk check passes)

    Returns (exit_code: int, stderr_text: str).
    """
    fake_engine = mock.MagicMock()
    fake_engine.uses_subprocess = False
    fake_engine.supports_per_chapter = True
    fake_engine.check_status.return_value = mock.MagicMock(available=True, reason="")

    fake_book = mock.MagicMock()
    fake_book.full_text = "x" * 1000  # 1k chars — text_chars > 0

    buf = io.StringIO()
    with (
        mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")),
        mock.patch(
            "src.app_config.load",
            return_value=mock.MagicMock(
                engine_id="edge", language="fi", voice_id="", output_mode="single"
            ),
        ),
        mock.patch(
            "src.synthesis_orchestrator.suggest_output_path",
            return_value="/tmp/out.mp3",
        ),
        mock.patch("src.engine_registry"),
        mock.patch("src.tts_base.get_engine", return_value=fake_engine),
        mock.patch(
            "src.synthesis_orchestrator.parse_book",
            return_value=fake_book,
        ),
        mock.patch(
            "src.system_checks.check_output_disk_space",
            return_value=disk_result,
        ),
        mock.patch("src.cli.convert._run_inprocess", return_value=synth_return),
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


# ---------------------------------------------------------------------------
# Test 1 — insufficient disk → EXIT_MISSING_DEP (2) with helpful message
# ---------------------------------------------------------------------------


class TestInsufficientDisk:
    def test_exits_with_missing_dep_code(self):
        args = _base_args()
        code, _ = _run_inner_with_mocks(
            args,
            disk_result=(False, 102.4, 10240.0),  # 0.1 GB free, 10 GB needed
        )
        assert code == EXIT_MISSING_DEP, (
            f"expected EXIT_MISSING_DEP ({EXIT_MISSING_DEP}), got {code}"
        )

    def test_error_message_contains_output_path(self):
        args = _base_args()
        _, stderr = _run_inner_with_mocks(
            args,
            disk_result=(False, 102.4, 10240.0),
        )
        # The output path resolved by the mock is /tmp/out.mp3.
        assert "/tmp/out.mp3" in stderr, (
            f"output path not mentioned in error message: {stderr!r}"
        )

    def test_error_message_contains_free_and_required_figures(self):
        args = _base_args()
        _, stderr = _run_inner_with_mocks(
            args,
            disk_result=(False, 102.4, 10240.0),
        )
        assert "102" in stderr, f"free MB figure missing from error: {stderr!r}"
        assert "10240" in stderr, f"required MB figure missing from error: {stderr!r}"


# ---------------------------------------------------------------------------
# Test 2 — sufficient disk → check passes, synthesis dispatched, EXIT_OK
# ---------------------------------------------------------------------------


class TestSufficientDisk:
    def test_synthesis_is_reached_and_returns_ok(self):
        args = _base_args()
        code, stderr = _run_inner_with_mocks(
            args,
            disk_result=(True, 51200.0, 1024.0),  # 50 GB free, 1 GB needed
            synth_return=EXIT_OK,
        )
        assert code == EXIT_OK, (
            f"expected EXIT_OK ({EXIT_OK}), got {code}\nstderr: {stderr}"
        )

    def test_no_preflight_error_in_stderr(self):
        args = _base_args()
        _, stderr = _run_inner_with_mocks(
            args,
            disk_result=(True, 51200.0, 1024.0),
            synth_return=EXIT_OK,
        )
        assert "insufficient disk" not in stderr.lower(), (
            f"unexpected preflight error in stderr: {stderr!r}"
        )


# ---------------------------------------------------------------------------
# Test 3 — --dry-run skips preflight entirely
# ---------------------------------------------------------------------------


class TestDryRunSkipsPreflight:
    def test_dry_run_exits_ok_even_with_no_space(self):
        """On --dry-run the preflight must never be called."""
        args = _base_args(dry_run=True)

        # check_output_disk_space raises if called — proves it's never reached.
        sentinel = Exception("preflight must not run on --dry-run")

        buf = io.StringIO()
        with (
            mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")),
            mock.patch(
                "src.app_config.load",
                return_value=mock.MagicMock(
                    engine_id="edge", language="fi", voice_id="", output_mode="single"
                ),
            ),
            mock.patch(
                "src.synthesis_orchestrator.suggest_output_path",
                return_value="/tmp/out.mp3",
            ),
            mock.patch(
                "src.system_checks.check_output_disk_space",
                side_effect=sentinel,
            ),
            mock.patch("sys.stderr", buf),
        ):
            code = convert._run_inner(
                args,
                input_path="dummy.txt",
                sample_text=None,
                json_mode=False,
                quiet=False,
                dry_run=True,
                stdin_tempfile=None,
            )

        assert code == EXIT_OK, (
            f"--dry-run should exit 0; got {code}\nstderr: {buf.getvalue()}"
        )


# ---------------------------------------------------------------------------
# Test 4 — parse failure: preflight skipped loudly, synthesis still runs
# ---------------------------------------------------------------------------


class TestParseFailureSkipsPreflightLoudly:
    """When parse_book raises, the preflight cannot estimate disk needs.

    The check is skipped but a clear message goes to stderr so the user
    knows the safety net was bypassed. Control falls through to
    synthesis, which will surface the real parse error in its own time.
    """

    def test_parse_error_logs_to_stderr_and_synthesis_runs(self):
        args = _base_args()

        fake_engine = mock.MagicMock()
        fake_engine.uses_subprocess = False
        fake_engine.supports_per_chapter = True
        fake_engine.check_status.return_value = mock.MagicMock(available=True, reason="")

        buf = io.StringIO()
        # check_output_disk_space must NOT be called when text_chars=0
        # (we skip the actual size comparison when we can't estimate).
        disk_check_mock = mock.Mock(
            side_effect=AssertionError("disk check must not run when parse fails"),
        )
        with (
            mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")),
            mock.patch(
                "src.app_config.load",
                return_value=mock.MagicMock(
                    engine_id="edge", language="fi", voice_id="", output_mode="single"
                ),
            ),
            mock.patch(
                "src.synthesis_orchestrator.suggest_output_path",
                return_value="/tmp/out.mp3",
            ),
            mock.patch("src.engine_registry"),
            mock.patch("src.tts_base.get_engine", return_value=fake_engine),
            mock.patch(
                "src.synthesis_orchestrator.parse_book",
                side_effect=ValueError("corrupt PDF"),
            ),
            mock.patch(
                "src.system_checks.check_output_disk_space",
                disk_check_mock,
            ),
            mock.patch("src.cli.convert._run_inprocess", return_value=EXIT_OK),
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

        # Synthesis was reached (preflight didn't block) and returned OK.
        assert code == EXIT_OK, f"expected EXIT_OK, got {code}\nstderr: {buf.getvalue()}"
        # User saw the breadcrumb that the preflight was skipped.
        stderr = buf.getvalue()
        assert "[preflight]" in stderr, f"missing preflight breadcrumb: {stderr!r}"
        assert "corrupt PDF" in stderr, f"missing exception detail: {stderr!r}"


# ---------------------------------------------------------------------------
# Test 5 — system_checks import failure: preflight skipped with stderr log
# ---------------------------------------------------------------------------


class TestSystemChecksImportFailureLogged:
    """If src.system_checks can't be imported the preflight skips, but
    the user sees a stderr breadcrumb instead of a silent no-op."""

    def test_importerror_logs_to_stderr_and_synthesis_runs(self):
        args = _base_args()

        fake_engine = mock.MagicMock()
        fake_engine.uses_subprocess = False
        fake_engine.supports_per_chapter = True
        fake_engine.check_status.return_value = mock.MagicMock(available=True, reason="")

        buf = io.StringIO()
        # Make the very first import (`from src.system_checks import
        # check_output_disk_space`) raise. We patch sys.modules so the
        # import fails the way it would on a broken install.
        import sys as _sys
        original_systemchecks = _sys.modules.get("src.system_checks")
        _sys.modules["src.system_checks"] = None  # next import raises ImportError

        try:
            with (
                mock.patch("src.cli.convert.validate_input_path", return_value=(EXIT_OK, "")),
                mock.patch(
                    "src.app_config.load",
                    return_value=mock.MagicMock(
                        engine_id="edge", language="fi", voice_id="", output_mode="single"
                    ),
                ),
                mock.patch(
                    "src.synthesis_orchestrator.suggest_output_path",
                    return_value="/tmp/out.mp3",
                ),
                mock.patch("src.engine_registry"),
                mock.patch("src.tts_base.get_engine", return_value=fake_engine),
                mock.patch("src.cli.convert._run_inprocess", return_value=EXIT_OK),
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
        finally:
            # Restore so other tests see the real module.
            if original_systemchecks is not None:
                _sys.modules["src.system_checks"] = original_systemchecks
            else:
                _sys.modules.pop("src.system_checks", None)

        assert code == EXIT_OK
        stderr = buf.getvalue()
        assert "[preflight]" in stderr
        assert "disk-space check unavailable" in stderr
