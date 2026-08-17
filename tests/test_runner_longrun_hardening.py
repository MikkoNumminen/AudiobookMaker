"""Tests for the long-run hardening in the Chatterbox runner.

All three come from one field incident: a ~14 hour conversion whose reported
RTF drifted 0.97 -> 1.61 overnight before the run stopped dead, on a book that
parsed as a single chapter with 2200+ chunks.

Diagnosis, against archived .chunk_stats.jsonl from three real runs on the dev
machine: VRAM was flat, generation speed was flat, and the RTF drift was an
artifact of charging discarded retries to the metric. The death was in chapter
finalization, which is both the most memory-hungry step and the most expensive
possible place to fail.
"""
from __future__ import annotations

import importlib.util
import struct
import sys
import wave
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def runner():
    """Import the runner script as a module (it is a script, not a package)."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "generate_chatterbox_audiobook.py"
    spec = importlib.util.spec_from_file_location("_abm_runner", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_abm_runner"] = mod
    spec.loader.exec_module(mod)
    return mod


def _float32_wav(path: Path, seconds: float = 0.5, rate: int = 24000) -> None:
    """Write a 32-bit float WAV, the format `ta.save` produces for chunks."""
    n = int(rate * seconds)
    samples = b"".join(struct.pack("<f", 0.5) for _ in range(n))
    hdr = b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVE"
    hdr += b"fmt " + struct.pack("<IHHIIHH", 16, 3, 1, rate, rate * 4, 4, 32)
    hdr += b"data" + struct.pack("<I", len(samples))
    path.write_bytes(hdr + samples)


class TestChunksAreNarrowedToSixteenBit:
    """The cache is 32-bit float. Two bugs followed from reading it as-is."""

    def test_cached_chunks_really_are_float32(self, tmp_path):
        """Pins the premise: if this ever changes, the fix below is moot."""
        p = tmp_path / "c.wav"
        _float32_wav(p)
        raw = p.read_bytes()
        assert struct.unpack("<H", raw[20:22])[0] == 3      # IEEE float
        assert struct.unpack("<H", raw[34:36])[0] == 32     # bits per sample

    def test_iter_trimmed_chunks_yields_sixteen_bit(self, tmp_path, runner):
        """`_vad_trim` divides samples by 32768.0, which is only correct for
        16-bit input. At width 4 the peak sample is 2**31, so Silero VAD was
        handed values up to 65536 and the trim silently did nothing."""
        pytest.importorskip("pydub")
        for chi in range(2):
            _float32_wav(tmp_path / f"ch01_chunk{chi:04d}.wav")

        segs = list(
            runner._iter_trimmed_chunks(tmp_path, 1, 2, None, None)
        )
        assert segs, "no chunks yielded"
        for seg in segs:
            assert seg.sample_width == 2

    def test_sample_scaling_lands_in_vad_range(self, tmp_path, runner):
        """The actual defect, expressed as the number that mattered."""
        pytest.importorskip("pydub")
        _float32_wav(tmp_path / "ch01_chunk0000.wav")
        seg = next(runner._iter_trimmed_chunks(tmp_path, 1, 1, None, None))
        peak = max(abs(v) for v in seg.get_array_of_samples())
        assert peak / 32768.0 <= 1.0


class TestRtfExcludesDiscardedRetries:
    """A rising retry rate must not read as a slowing engine."""

    def test_best_dt_is_the_metric_not_the_total(self, runner):
        """`dt += dt_r` charges every re-roll to the wall clock. Dividing that
        by the winner's audio alone reported four attempts as four times the
        generation cost, which is how 1.03 became 1.61."""
        src = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "generate_chatterbox_audiobook.py"
        ).read_text(encoding="utf-8")
        assert '"rtf": round(best_dt / audio_s, 3)' in src
        assert "rtf = best_dt / audio_s" in src

    def test_wall_clock_is_still_recorded(self, runner):
        """Retries are real time the user waits; the ETA needs them."""
        src = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "generate_chatterbox_audiobook.py"
        ).read_text(encoding="utf-8")
        assert '"wall_rtf": round(dt / audio_s, 3)' in src
        assert '"synth_s": round(dt, 3)' in src


class TestAssemblyFailuresAreReported:
    def test_memory_error_is_caught_and_prefixed(self, runner):
        """Assembly is the most expensive place to fail: every chunk is
        already synthesized. A bare traceback reaches the GUI as ordinary log
        lines, because only `[error]` is parsed into an error event."""
        src = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "generate_chatterbox_audiobook.py"
        ).read_text(encoding="utf-8")
        assert "except MemoryError:" in src
        assert "[error] ran out of memory assembling chapter" in src

    def test_the_message_says_the_work_is_not_lost(self, runner):
        src = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "generate_chatterbox_audiobook.py"
        ).read_text(encoding="utf-8")
        assert src.count("nothing was lost") >= 2

    def test_stop_requested_is_not_swallowed(self, runner):
        """Cancel must stay a clean stop, not become an assembly error."""
        src = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "generate_chatterbox_audiobook.py"
        ).read_text(encoding="utf-8")
        i = src.index("[error] ran out of memory assembling chapter")
        window = src[max(0, i - 1200):i]
        assert "except _StopRequested:" in window
        assert "raise" in window
