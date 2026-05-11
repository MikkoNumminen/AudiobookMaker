"""Unit tests for :mod:`src.voice_pack.ffmpeg_slice`.

Hermetic — every test injects a fake ``runner`` so no real ffmpeg is
spawned. The only thing we cannot fake is ``shutil.which``; tests
that exercise the resolver path use ``ffmpeg_exe`` / ``ffprobe_exe``
overrides instead.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.voice_pack.ffmpeg_slice import (
    FfmpegError,
    SliceRequest,
    probe_duration,
    slice_audio,
)


def _completed(
    *, returncode: int, stdout: str = "", stderr: str = "",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


# ---------------------------------------------------------------------------
# probe_duration
# ---------------------------------------------------------------------------


class TestProbeDuration:
    def test_parses_duration_from_json(self, tmp_path: Path) -> None:
        captured: dict = {}

        def fake_runner(cmd: list[str], env: dict):
            captured["cmd"] = cmd
            return _completed(
                returncode=0,
                stdout=json.dumps({"format": {"duration": "123.456"}}),
            )

        out = probe_duration(
            tmp_path / "fake.wav",
            ffprobe_exe="ffprobe",
            runner=fake_runner,
        )
        assert out == pytest.approx(123.456)
        # The argv must include the json output flag, not csv etc.
        assert "json" in captured["cmd"]

    def test_nonzero_exit_raises(self, tmp_path: Path) -> None:
        def fake_runner(cmd: list[str], env: dict):
            return _completed(returncode=2, stderr="Invalid data found")

        with pytest.raises(FfmpegError) as excinfo:
            probe_duration(
                tmp_path / "fake.wav",
                ffprobe_exe="ffprobe",
                runner=fake_runner,
            )
        assert "code 2" in str(excinfo.value)
        assert "Invalid data" in str(excinfo.value)

    def test_unparseable_json_raises(self, tmp_path: Path) -> None:
        def fake_runner(cmd: list[str], env: dict):
            return _completed(returncode=0, stdout="this is not json")

        with pytest.raises(FfmpegError):
            probe_duration(
                tmp_path / "fake.wav",
                ffprobe_exe="ffprobe",
                runner=fake_runner,
            )

    def test_missing_duration_field_raises(self, tmp_path: Path) -> None:
        def fake_runner(cmd: list[str], env: dict):
            return _completed(returncode=0, stdout=json.dumps({}))

        with pytest.raises(FfmpegError):
            probe_duration(
                tmp_path / "fake.wav",
                ffprobe_exe="ffprobe",
                runner=fake_runner,
            )


# ---------------------------------------------------------------------------
# slice_audio
# ---------------------------------------------------------------------------


class TestSliceAudio:
    def test_writes_output_and_passes_correct_args(self, tmp_path: Path) -> None:
        captured: dict = {}

        def fake_runner(cmd: list[str], env: dict):
            captured["cmd"] = cmd
            # Touch the output file so the post-condition check passes.
            out = Path(cmd[-1])
            out.write_bytes(b"RIFF...")
            return _completed(returncode=0)

        req = SliceRequest(
            source=tmp_path / "src.wav",
            out_path=tmp_path / "chunk_0.wav",
            start_seconds=12.5,
            end_seconds=312.5,
        )
        out = slice_audio(req, ffmpeg_exe="ffmpeg", runner=fake_runner)
        assert out == req.out_path
        assert out.exists()
        # Verify the slice math went into the argv as expected.
        cmd = captured["cmd"]
        assert "-ss" in cmd
        assert cmd[cmd.index("-ss") + 1] == "12.500"
        assert "-t" in cmd
        # 312.5 - 12.5 = 300.0
        assert cmd[cmd.index("-t") + 1] == "300.000"
        assert "-ar" in cmd
        assert cmd[cmd.index("-ar") + 1] == "16000"

    def test_zero_or_negative_duration_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            slice_audio(
                SliceRequest(
                    source=tmp_path / "x.wav",
                    out_path=tmp_path / "y.wav",
                    start_seconds=10.0,
                    end_seconds=10.0,
                ),
                ffmpeg_exe="ffmpeg",
                runner=lambda cmd, env: _completed(returncode=0),
            )

    def test_nonzero_exit_raises(self, tmp_path: Path) -> None:
        def fake_runner(cmd: list[str], env: dict):
            return _completed(returncode=1, stderr="bad seek")

        with pytest.raises(FfmpegError):
            slice_audio(
                SliceRequest(
                    source=tmp_path / "src.wav",
                    out_path=tmp_path / "chunk.wav",
                    start_seconds=0.0,
                    end_seconds=10.0,
                ),
                ffmpeg_exe="ffmpeg",
                runner=fake_runner,
            )

    def test_no_output_file_after_zero_exit_raises(self, tmp_path: Path) -> None:
        # Defensive: ffmpeg said success but the file isn't there. We
        # surface that as an FfmpegError rather than letting downstream
        # code blow up on the missing file later.
        def fake_runner(cmd: list[str], env: dict):
            return _completed(returncode=0)  # no file written

        with pytest.raises(FfmpegError) as excinfo:
            slice_audio(
                SliceRequest(
                    source=tmp_path / "src.wav",
                    out_path=tmp_path / "chunk.wav",
                    start_seconds=0.0,
                    end_seconds=10.0,
                ),
                ffmpeg_exe="ffmpeg",
                runner=fake_runner,
            )
        assert "no file" in str(excinfo.value).lower()

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        def fake_runner(cmd: list[str], env: dict):
            out = Path(cmd[-1])
            out.write_bytes(b"x")
            return _completed(returncode=0)

        nested = tmp_path / "deep" / "a" / "b" / "out.wav"
        slice_audio(
            SliceRequest(
                source=tmp_path / "src.wav",
                out_path=nested,
                start_seconds=0.0,
                end_seconds=1.0,
            ),
            ffmpeg_exe="ffmpeg",
            runner=fake_runner,
        )
        assert nested.exists()
