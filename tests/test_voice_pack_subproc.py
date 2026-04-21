"""Tests for :mod:`src.voice_pack_subproc`.

Hermetic. We never launch a real subprocess — a fake ``SubprocessFactory``
returns a stub with a canned stdout iterable and a settable returncode.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterable, Optional

import pytest

from src.voice_pack_subproc import (
    STAGE_ASR,
    STAGE_BUCKET,
    STAGE_DIARIZE,
    STAGE_DONE,
    STAGE_ERROR,
    STAGE_LINE,
    STAGE_STARTING,
    STAGE_WRITE,
    AnalyzeProgress,
    build_analyze_argv,
    run_analyze,
    _parse_stamp,
)


# ---------------------------------------------------------------------------
# fake subprocess
# ---------------------------------------------------------------------------


class _FakeProc:
    """Stands in for :class:`subprocess.Popen`.

    ``lines`` is the canned stdout. ``returncode`` is what ``wait()``
    will yield. ``terminated`` is flipped by :meth:`terminate` so tests
    can assert cancel actually called through.
    """

    def __init__(
        self,
        lines: Iterable[str],
        returncode: int = 0,
        *,
        on_line: Optional[callable] = None,
    ) -> None:
        self._on_line = on_line
        self.stdout = self._gen(list(lines))
        self.returncode = returncode
        self.terminated = False

    def _gen(self, lines: list[str]):
        for line in lines:
            if self._on_line is not None:
                self._on_line(line)
            # Emit a trailing newline the way real Popen.stdout does.
            yield line if line.endswith("\n") else line + "\n"

    def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True


def _factory(proc: _FakeProc):
    """Return a ``SubprocessFactory`` that yields ``proc`` once."""
    def _f(cmd: list[str], env: dict) -> _FakeProc:
        # Store the argv/env on the proc so tests can assert on them.
        proc.received_cmd = cmd  # type: ignore[attr-defined]
        proc.received_env = env  # type: ignore[attr-defined]
        return proc

    return _f


# ---------------------------------------------------------------------------
# build_analyze_argv
# ---------------------------------------------------------------------------


class TestBuildAnalyzeArgv:
    def test_exact_num_speakers(self) -> None:
        argv = build_analyze_argv(
            python_exe=Path("py.exe"),
            script_path=Path("analyze.py"),
            wav=Path("in.wav"),
            out_dir=Path("out"),
            num_speakers=2,
        )
        assert "--num-speakers" in argv
        assert argv[argv.index("--num-speakers") + 1] == "2"
        assert "--min-speakers" not in argv
        assert "--max-speakers" not in argv
        assert "-v" in argv

    def test_min_max_range(self) -> None:
        argv = build_analyze_argv(
            python_exe=Path("py.exe"),
            script_path=Path("analyze.py"),
            wav=Path("in.wav"),
            out_dir=Path("out"),
            min_speakers=3,
            max_speakers=8,
        )
        assert "--min-speakers" in argv
        assert "--max-speakers" in argv
        assert "--num-speakers" not in argv

    def test_num_overrides_min_max(self) -> None:
        # When exact is set, range is silently dropped — pyannote's
        # own behaviour, mirrored in the argv builder.
        argv = build_analyze_argv(
            python_exe=Path("py.exe"),
            script_path=Path("analyze.py"),
            wav=Path("in.wav"),
            out_dir=Path("out"),
            num_speakers=1,
            min_speakers=3,
            max_speakers=8,
        )
        assert "--num-speakers" in argv
        assert "--min-speakers" not in argv

    def test_diarizer_passed(self) -> None:
        argv = build_analyze_argv(
            python_exe=Path("py.exe"),
            script_path=Path("analyze.py"),
            wav=Path("in.wav"),
            out_dir=Path("out"),
            diarizer="ecapa",
        )
        assert "--diarizer" in argv
        assert argv[argv.index("--diarizer") + 1] == "ecapa"

    def test_auto_detect_omits_speaker_flags(self) -> None:
        argv = build_analyze_argv(
            python_exe=Path("py.exe"),
            script_path=Path("analyze.py"),
            wav=Path("in.wav"),
            out_dir=Path("out"),
        )
        assert "--num-speakers" not in argv
        assert "--min-speakers" not in argv
        assert "--max-speakers" not in argv


# ---------------------------------------------------------------------------
# _parse_stamp
# ---------------------------------------------------------------------------


class TestParseStamp:
    def test_asr_stamp(self) -> None:
        out = _parse_stamp("[voice_pack_analyze] asr: 12.34s")
        assert out == ("asr", 12.34)

    def test_diarize_stamp(self) -> None:
        out = _parse_stamp("[voice_pack_analyze] diarize: 5.0s")
        assert out == ("diarize", 5.0)

    def test_integer_elapsed_ok(self) -> None:
        out = _parse_stamp("[voice_pack_analyze] bucket: 1s")
        assert out == ("bucket", 1.0)

    def test_non_stamp_returns_none(self) -> None:
        assert _parse_stamp("hello world") is None

    def test_unknown_stage_returns_none(self) -> None:
        # Guards against accidental future emitter strings being
        # misclassified — only the four canonical stages are accepted.
        assert _parse_stamp("[voice_pack_analyze] training: 1.0s") is None

    def test_embedded_in_longer_line_still_matches(self) -> None:
        # The real script prints the stamp verbatim, but pip warnings
        # sometimes prefix stdout lines. Regex uses search() so that
        # keeps working.
        out = _parse_stamp("INFO: [voice_pack_analyze] write: 0.05s done")
        assert out == ("write", 0.05)


# ---------------------------------------------------------------------------
# run_analyze end-to-end with injected subprocess
# ---------------------------------------------------------------------------


class TestRunAnalyze:
    def test_success_streams_each_stage(self, tmp_path: Path) -> None:
        proc = _FakeProc(
            lines=[
                "Loading ASR model…",
                "[voice_pack_analyze] asr: 12.50s",
                "[voice_pack_analyze] diarize: 8.10s",
                "[voice_pack_analyze] bucket: 0.22s",
                "[voice_pack_analyze] write: 0.04s",
            ],
            returncode=0,
        )
        events: list[AnalyzeProgress] = []

        result = run_analyze(
            wav=tmp_path / "in.wav",
            out_dir=tmp_path / "out",
            num_speakers=1,
            progress_cb=events.append,
            python_exe=Path("fake_python"),
            script_path=Path("fake_analyze.py"),
            subprocess_factory=_factory(proc),
        )

        assert result.ok is True
        assert result.return_code == 0
        assert result.error is None

        stages = [e.stage for e in events]
        assert stages[0] == STAGE_STARTING
        assert STAGE_ASR in stages
        assert STAGE_DIARIZE in stages
        assert STAGE_BUCKET in stages
        assert STAGE_WRITE in stages
        assert stages[-1] == STAGE_DONE

        # Non-stamp output comes through as STAGE_LINE, not dropped.
        line_events = [e for e in events if e.stage == STAGE_LINE]
        assert any("Loading ASR model" in e.message for e in line_events)

        # Timing stamps carry elapsed_s.
        asr_event = next(e for e in events if e.stage == STAGE_ASR)
        assert asr_event.elapsed_s == pytest.approx(12.5)

    def test_nonzero_exit_surfaces_error(self, tmp_path: Path) -> None:
        proc = _FakeProc(
            lines=["Traceback (most recent call last):", "RuntimeError: boom"],
            returncode=3,
        )
        events: list[AnalyzeProgress] = []

        result = run_analyze(
            wav=tmp_path / "in.wav",
            out_dir=tmp_path / "out",
            progress_cb=events.append,
            python_exe=Path("fake_python"),
            script_path=Path("fake.py"),
            subprocess_factory=_factory(proc),
        )

        assert result.ok is False
        assert result.return_code == 3
        assert result.error is not None
        assert "code 3" in result.error
        assert events[-1].stage == STAGE_ERROR

    def test_cancel_terminates_proc(self, tmp_path: Path) -> None:
        cancel = threading.Event()
        # Fire the cancel event as soon as the first stdout line is
        # yielded — simulates a user hitting Cancel mid-ASR.
        proc = _FakeProc(
            lines=["about to work…", "[voice_pack_analyze] asr: 1s"],
            returncode=0,
            on_line=lambda line: cancel.set(),
        )

        result = run_analyze(
            wav=tmp_path / "in.wav",
            out_dir=tmp_path / "out",
            progress_cb=None,
            cancel_event=cancel,
            python_exe=Path("fake_python"),
            script_path=Path("fake.py"),
            subprocess_factory=_factory(proc),
        )

        assert result.ok is False
        assert result.error == "Cancelled"
        assert proc.terminated is True

    def test_hf_token_passed_via_env_not_argv(self, tmp_path: Path) -> None:
        proc = _FakeProc(lines=[], returncode=0)
        run_analyze(
            wav=tmp_path / "in.wav",
            out_dir=tmp_path / "out",
            hf_token="hf_secret_example_not_real",
            python_exe=Path("fake_python"),
            script_path=Path("fake.py"),
            subprocess_factory=_factory(proc),
        )
        # Token must NOT appear in argv — argv is visible in ps.
        assert not any(
            "hf_secret_example_not_real" in part for part in proc.received_cmd  # type: ignore[attr-defined]
        )
        # But it must be set in the child's env.
        assert proc.received_env["HF_TOKEN"] == "hf_secret_example_not_real"  # type: ignore[attr-defined]

    def test_oserror_on_launch_returns_error_result(
        self, tmp_path: Path
    ) -> None:
        def _boom(cmd: list[str], env: dict):
            raise OSError("no such executable")

        events: list[AnalyzeProgress] = []
        result = run_analyze(
            wav=tmp_path / "in.wav",
            out_dir=tmp_path / "out",
            progress_cb=events.append,
            python_exe=Path("fake_python"),
            script_path=Path("fake.py"),
            subprocess_factory=_boom,
        )
        assert result.ok is False
        assert result.error is not None
        assert "Could not launch" in result.error
        assert events[-1].stage == STAGE_ERROR

    def test_log_lines_accumulate(self, tmp_path: Path) -> None:
        proc = _FakeProc(
            lines=["alpha", "beta", "[voice_pack_analyze] write: 0.01s"],
            returncode=0,
        )
        result = run_analyze(
            wav=tmp_path / "in.wav",
            out_dir=tmp_path / "out",
            python_exe=Path("fake_python"),
            script_path=Path("fake.py"),
            subprocess_factory=_factory(proc),
        )
        assert "alpha" in result.log_lines
        assert "beta" in result.log_lines

    def test_out_dir_created_if_missing(self, tmp_path: Path) -> None:
        proc = _FakeProc(lines=[], returncode=0)
        out_dir = tmp_path / "deep" / "out"
        assert not out_dir.exists()

        run_analyze(
            wav=tmp_path / "in.wav",
            out_dir=out_dir,
            python_exe=Path("fake_python"),
            script_path=Path("fake.py"),
            subprocess_factory=_factory(proc),
        )
        assert out_dir.is_dir()

    def test_result_paths_point_into_out_dir(self, tmp_path: Path) -> None:
        proc = _FakeProc(lines=[], returncode=0)
        out_dir = tmp_path / "outxx"
        result = run_analyze(
            wav=tmp_path / "in.wav",
            out_dir=out_dir,
            python_exe=Path("fake_python"),
            script_path=Path("fake.py"),
            subprocess_factory=_factory(proc),
        )
        assert result.transcripts_path == out_dir / "transcripts.jsonl"
        assert result.speakers_yaml_path == out_dir / "speakers.yaml"
        assert result.report_path == out_dir / "report.md"
