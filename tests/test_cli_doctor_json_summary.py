"""Tests for doctor --json summary event (N6 fix).

Verifies that `doctor --json` emits a terminal `{"kind": "summary", ...}` line
that script consumers can use to determine pass/fail without aggregating every
per-check row.
"""

from __future__ import annotations

import json
import os
import sys
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cli(*args: str) -> "subprocess.CompletedProcess[str]":
    import subprocess

    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def _parse_ndjson(text: str) -> list[dict]:
    return [json.loads(line) for line in text.strip().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# 1. Summary line is always the last line in --json mode
# ---------------------------------------------------------------------------


class TestDoctorJsonSummaryLast:
    def test_doctor_json_emits_summary_last(self):
        result = _cli("doctor", "--json")
        assert result.returncode in (0, 2)
        lines = _parse_ndjson(result.stdout)
        assert lines, "expected at least one JSON line"
        last = lines[-1]
        assert last.get("kind") == "summary", (
            f"last JSON line should have kind='summary', got: {last}"
        )


# ---------------------------------------------------------------------------
# 2. Summary shows pass when all checks healthy
# ---------------------------------------------------------------------------


class TestDoctorJsonSummaryPass:
    def test_doctor_json_summary_pass_when_everything_ok(self):
        """When ffmpeg is present and at least one engine is available the
        summary must report status='pass', required_missing=[], exit_code=0."""
        import contextlib
        import io

        from src.cli.__main__ import main

        # A minimal fake GPU result (not required — just needs not to crash).
        class _FakeGPU:
            has_nvidia = False

        class _FakeDisk:
            free_gb = 50.0
            total_gb = 100.0
            path = "/fake"

        class _FakeStatus:
            available = True
            reason = ""

        class _FakeEngine:
            id = "edge"
            display_name = "Edge TTS"

            def check_status(self):
                return _FakeStatus()

        buf = io.StringIO()
        with (
            mock.patch("src.ffmpeg_path.get_ffmpeg_exe", return_value="/usr/bin/ffmpeg"),
            mock.patch("src.system_checks.detect_gpu", return_value=_FakeGPU()),
            mock.patch("src.system_checks.check_disk_space", return_value=_FakeDisk()),
            mock.patch("src.tts_base.list_engines", return_value=[_FakeEngine()]),
            contextlib.redirect_stdout(buf),
        ):
            rc = main(["doctor", "--json"])

        assert rc == 0

        lines = _parse_ndjson(buf.getvalue())
        assert lines, "expected JSON lines"
        summary = lines[-1]
        assert summary["kind"] == "summary"
        assert summary["status"] == "pass"
        assert summary["required_missing"] == []
        assert summary["exit_code"] == 0


# ---------------------------------------------------------------------------
# 3. Summary lists the missing required component when ffmpeg is absent
# ---------------------------------------------------------------------------


class TestDoctorJsonSummaryFail:
    def test_doctor_json_summary_fail_lists_missing_required(self):
        """When ffmpeg is absent the summary must report status='fail',
        'ffmpeg' in required_missing, and exit_code=2."""
        import io
        import contextlib

        from src.cli.__main__ import main

        class _FakeGPU:
            has_nvidia = False

        class _FakeDisk:
            free_gb = 50.0
            total_gb = 100.0
            path = "/fake"

        class _FakeStatus:
            available = True
            reason = ""

        class _FakeEngine:
            id = "edge"
            display_name = "Edge TTS"

            def check_status(self):
                return _FakeStatus()

        buf = io.StringIO()
        with (
            mock.patch("src.ffmpeg_path.get_ffmpeg_exe", return_value=None),  # missing
            mock.patch("src.system_checks.detect_gpu", return_value=_FakeGPU()),
            mock.patch("src.system_checks.check_disk_space", return_value=_FakeDisk()),
            mock.patch("src.tts_base.list_engines", return_value=[_FakeEngine()]),
            contextlib.redirect_stdout(buf),
        ):
            rc = main(["doctor", "--json"])

        assert rc == 2

        lines = _parse_ndjson(buf.getvalue())
        assert lines, "expected JSON lines"
        summary = lines[-1]
        assert summary["kind"] == "summary"
        assert summary["status"] == "fail"
        assert "ffmpeg" in summary["required_missing"]
        assert summary["exit_code"] == 2


# ---------------------------------------------------------------------------
# 4. Human-readable mode produces no summary JSON line
# ---------------------------------------------------------------------------


class TestDoctorNoSummaryHumanMode:
    def test_doctor_no_summary_in_human_mode(self):
        """Without --json the output should be a plain table, not JSON."""
        result = _cli("doctor")
        assert result.returncode in (0, 2)
        # The human output must not contain a JSON summary object.
        for line in result.stdout.strip().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
                # If it parses as JSON it must NOT be our summary.
                assert obj.get("kind") != "summary", (
                    f"human mode should not emit summary JSON; got: {stripped}"
                )
            except json.JSONDecodeError:
                pass  # plain text is expected
