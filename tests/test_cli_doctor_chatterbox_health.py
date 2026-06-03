"""doctor surfaces Chatterbox venv import/version-drift health.

check_status() is presence-only, so a venv that exists but fails to load
(drifted transformers -> the 'LlamaModel' failure) would otherwise read as
healthy. doctor runs version_manifest.probe_venv() to catch and name it.
"""

from __future__ import annotations

import contextlib
import io
import json
from unittest import mock

import pytest

from src.cli.__main__ import main
from src.version_manifest import Drift, VenvHealth


def _parse_ndjson(text: str) -> list[dict]:
    return [json.loads(line) for line in text.strip().splitlines() if line.strip()]


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


def _run_doctor(resolve_return, probe_return) -> list[dict]:
    buf = io.StringIO()
    with (
        mock.patch("src.ffmpeg_path.get_ffmpeg_exe", return_value="/usr/bin/ffmpeg"),
        mock.patch("src.system_checks.detect_gpu", return_value=_FakeGPU()),
        mock.patch("src.system_checks.check_disk_space", return_value=_FakeDisk()),
        mock.patch("src.tts_base.list_engines", return_value=[_FakeEngine()]),
        mock.patch(
            "src.launcher_bridge.resolve_chatterbox_python",
            return_value=resolve_return,
        ),
        mock.patch("src.version_manifest.probe_venv", return_value=probe_return),
        contextlib.redirect_stdout(buf),
    ):
        main(["doctor", "--json"])
    return _parse_ndjson(buf.getvalue())


def _health_row(lines: list[dict]) -> dict | None:
    for line in lines:
        if line.get("name") == "engine:chatterbox_grandmom:health":
            return line
    return None


def test_doctor_flags_broken_drifted_venv() -> None:
    broken = VenvHealth(
        ok=False,
        import_error=(
            "RuntimeError: Could not import module 'LlamaModel'. "
            "Are this object's requirements defined correctly?"
        ),
        drift=[Drift(package="transformers", expected="5.2.0", installed="5.4.0")],
    )
    lines = _run_doctor("C:/AudiobookMaker/.venv-chatterbox/python.exe", broken)
    row = _health_row(lines)
    assert row is not None, "expected a chatterbox health row"
    assert row["status"] == "error"
    assert "transformers" in row["detail"]
    assert "repair" in row["detail"].lower()


def test_doctor_reports_healthy_venv() -> None:
    lines = _run_doctor(
        "C:/AudiobookMaker/.venv-chatterbox/python.exe", VenvHealth(ok=True)
    )
    row = _health_row(lines)
    assert row is not None
    assert row["status"] == "ok"


def test_doctor_skips_health_row_when_no_venv() -> None:
    # resolve_chatterbox_python returns None -> no probe, no health row.
    lines = _run_doctor(None, VenvHealth(ok=True))
    assert _health_row(lines) is None


def test_doctor_probe_failure_does_not_claim_broken() -> None:
    unknown = VenvHealth(probe_failed="probe timed out after 60s")
    lines = _run_doctor("C:/AudiobookMaker/.venv-chatterbox/python.exe", unknown)
    row = _health_row(lines)
    assert row is not None
    # Inconclusive probe must not be reported as a hard "error".
    assert row["status"] != "error"
