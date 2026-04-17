"""Tests for :mod:`src.voice_pack.dataset`.

The tests deliberately avoid pydub / ffmpeg by injecting a fake audio slicer
that just writes a small byte string to the output path. That keeps the
test suite hermetic and fast — the real slicer is exercised in integration
tests, not here.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from src.voice_pack.dataset import export_dataset, rebalance_chunks
from src.voice_pack.types import TaggedChunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(
    *,
    start: float = 0.0,
    end: float = 1.0,
    text: str = "hello",
    speaker: str = "SPEAKER_00",
    confidence: float = 1.0,
    emotion: str = "neutral",
    emotion_confidence: float = 1.0,
) -> TaggedChunk:
    """Construct a :class:`TaggedChunk` with sensible defaults for tests."""
    return TaggedChunk(
        start=start,
        end=end,
        text=text,
        speaker=speaker,
        confidence=confidence,
        emotion=emotion,
        emotion_confidence=emotion_confidence,
    )


def _fake_slicer(
    src: Path, start_s: float, end_s: float, out_path: Path, target_sr: int
) -> None:
    """Stand-in for the pydub slicer. Just touches the file with dummy bytes."""
    out_path.write_bytes(b"FAKEWAV")


class _RecordingSlicer:
    """Slicer that records every call. Handy for arg-capture assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, float, float, Path, int]] = []

    def __call__(
        self,
        src: Path,
        start_s: float,
        end_s: float,
        out_path: Path,
        target_sr: int,
    ) -> None:
        self.calls.append((src, start_s, end_s, out_path, target_sr))
        out_path.write_bytes(b"FAKEWAV")


# ---------------------------------------------------------------------------
# export_dataset
# ---------------------------------------------------------------------------


def test_export_dataset_creates_files(tmp_path: Path) -> None:
    chunks = [
        _make_chunk(start=0.0, end=1.5, text="one"),
        _make_chunk(start=2.0, end=3.25, text="two"),
        _make_chunk(start=4.0, end=5.0, text="three"),
    ]

    export_dataset(
        chunks,
        source_audio_path=tmp_path / "source.wav",
        out_dir=tmp_path / "ds",
        audio_slicer=_fake_slicer,
    )

    out = tmp_path / "ds"
    assert (out / "wavs" / "0000.wav").exists()
    assert (out / "wavs" / "0001.wav").exists()
    assert (out / "wavs" / "0002.wav").exists()
    assert (out / "metadata.csv").exists()
    assert (out / "manifest.json").exists()


def test_export_dataset_manifest_content(tmp_path: Path) -> None:
    chunks = [
        _make_chunk(start=0.0, end=1.0, text="a", emotion="neutral"),
        _make_chunk(start=1.0, end=2.5, text="b", emotion="angry"),
        _make_chunk(start=3.0, end=4.0, text="c", emotion="neutral"),
    ]

    manifest = export_dataset(
        chunks,
        source_audio_path="/fake/src.wav",
        out_dir=tmp_path / "ds",
        audio_slicer=_fake_slicer,
        sample_rate_hz=22050,
    )

    assert manifest.speaker == "SPEAKER_00"
    assert manifest.total_seconds == pytest.approx(1.0 + 1.5 + 1.0)
    assert manifest.emotion_counts == {"neutral": 2, "angry": 1}
    assert manifest.sample_rate_hz == 22050
    assert [c.path for c in manifest.clips] == [
        "wavs/0000.wav",
        "wavs/0001.wav",
        "wavs/0002.wav",
    ]

    # The on-disk manifest.json should match the in-memory manifest.
    with (tmp_path / "ds" / "manifest.json").open(encoding="utf-8") as fh:
        loaded: dict[str, Any] = json.load(fh)
    assert loaded["speaker"] == "SPEAKER_00"
    assert loaded["sample_rate_hz"] == 22050
    assert loaded["emotion_counts"] == {"neutral": 2, "angry": 1}
    assert len(loaded["clips"]) == 3
    assert loaded["clips"][0]["path"] == "wavs/0000.wav"


def test_export_dataset_metadata_csv_format(tmp_path: Path) -> None:
    # Non-ASCII Finnish text to prove UTF-8 survives the round trip.
    chunks = [
        _make_chunk(start=0.0, end=1.234, text="Kärsimys!", emotion="angry"),
        _make_chunk(start=2.0, end=2.5, text="Hyvää yötä", emotion="neutral"),
    ]

    export_dataset(
        chunks,
        source_audio_path="/fake/src.wav",
        out_dir=tmp_path / "ds",
        audio_slicer=_fake_slicer,
    )

    csv_path = tmp_path / "ds" / "metadata.csv"
    text = csv_path.read_text(encoding="utf-8")
    lines = text.strip("\n").split("\n")

    assert len(lines) == 2
    # Row format: 'nnnn|text|emotion|duration' — but the path includes wavs/.
    assert lines[0] == "wavs/0000|Kärsimys!|angry|1.234"
    assert lines[1] == "wavs/0001|Hyvää yötä|neutral|0.500"

    # Pipe-delimited, exactly 3 pipes per row.
    for line in lines:
        assert line.count("|") == 3


def test_export_dataset_heterogeneous_speakers_raises(tmp_path: Path) -> None:
    chunks = [
        _make_chunk(speaker="SPEAKER_00"),
        _make_chunk(speaker="SPEAKER_01"),
    ]

    with pytest.raises(ValueError) as exc_info:
        export_dataset(
            chunks,
            source_audio_path="/fake/src.wav",
            out_dir=tmp_path / "ds",
            audio_slicer=_fake_slicer,
        )
    msg = str(exc_info.value)
    assert "SPEAKER_00" in msg
    assert "SPEAKER_01" in msg


def test_export_dataset_slicer_called_correctly(tmp_path: Path) -> None:
    chunks = [
        _make_chunk(start=0.0, end=1.5),
        _make_chunk(start=2.5, end=4.0),
    ]
    source = tmp_path / "src.wav"
    recorder = _RecordingSlicer()

    export_dataset(
        chunks,
        source_audio_path=source,
        out_dir=tmp_path / "ds",
        audio_slicer=recorder,
        sample_rate_hz=16000,
    )

    assert len(recorder.calls) == 2

    src0, start0, end0, out0, sr0 = recorder.calls[0]
    assert Path(src0) == source
    assert start0 == 0.0
    assert end0 == 1.5
    assert out0 == tmp_path / "ds" / "wavs" / "0000.wav"
    assert sr0 == 16000

    src1, start1, end1, out1, sr1 = recorder.calls[1]
    assert Path(src1) == source
    assert start1 == 2.5
    assert end1 == 4.0
    assert out1 == tmp_path / "ds" / "wavs" / "0001.wav"
    assert sr1 == 16000


def test_export_dataset_empty_chunks(tmp_path: Path) -> None:
    manifest = export_dataset(
        [],
        source_audio_path="/fake/src.wav",
        out_dir=tmp_path / "ds",
        audio_slicer=_fake_slicer,
    )

    assert manifest.clips == []
    assert manifest.total_seconds == 0.0
    assert manifest.emotion_counts == {}

    wavs_dir = tmp_path / "ds" / "wavs"
    assert wavs_dir.is_dir()
    assert list(wavs_dir.iterdir()) == []

    csv_text = (tmp_path / "ds" / "metadata.csv").read_text(encoding="utf-8")
    assert csv_text == ""

    # manifest.json still present.
    manifest_data = json.loads(
        (tmp_path / "ds" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest_data["clips"] == []


def test_export_dataset_rebalance_flag_effect(tmp_path: Path) -> None:
    # 5 neutral vs 1 angry. With rebalance on, the angry class gets upsampled
    # so the slicer should be called 5 + 5 = 10 times instead of 6.
    chunks = [
        _make_chunk(start=float(i), end=float(i) + 0.5, emotion="neutral", text=f"n{i}")
        for i in range(5)
    ]
    chunks.append(_make_chunk(start=10.0, end=10.5, emotion="angry", text="a"))

    recorder_off = _RecordingSlicer()
    export_dataset(
        chunks,
        source_audio_path="/fake/src.wav",
        out_dir=tmp_path / "off",
        audio_slicer=recorder_off,
        rebalance_by_emotion=False,
    )
    assert len(recorder_off.calls) == len(chunks)

    recorder_on = _RecordingSlicer()
    manifest = export_dataset(
        chunks,
        source_audio_path="/fake/src.wav",
        out_dir=tmp_path / "on",
        audio_slicer=recorder_on,
        rebalance_by_emotion=True,
    )
    assert len(recorder_on.calls) > len(chunks)

    # Filenames are monotonic 0000, 0001, ...
    filenames = sorted(p.name for p in (tmp_path / "on" / "wavs").iterdir())
    expected = [f"{i:04d}.wav" for i in range(len(manifest.clips))]
    assert filenames == expected


# ---------------------------------------------------------------------------
# rebalance_chunks
# ---------------------------------------------------------------------------


def test_rebalance_chunks_upsamples_to_max() -> None:
    chunks: list[TaggedChunk] = []
    chunks.extend(
        _make_chunk(start=float(i), end=float(i) + 0.1, emotion="neutral", text=f"n{i}")
        for i in range(10)
    )
    chunks.extend(
        _make_chunk(start=100.0 + i, end=100.5 + i, emotion="angry", text=f"a{i}")
        for i in range(2)
    )
    chunks.append(_make_chunk(start=200.0, end=200.5, emotion="happy", text="h0"))

    result = rebalance_chunks(chunks)
    counts = Counter(c.emotion for c in result)

    # All three classes should end up at the majority size (10).
    assert counts["neutral"] == 10
    assert counts["angry"] == 10
    assert counts["happy"] == 10
    assert len(result) == 30


def test_rebalance_chunks_custom_target() -> None:
    """With an explicit target, upsample below it AND downsample above it.

    Design choice documented here: the function treats ``target_per_emotion``
    as an exact quota. 10 neutral chunks become 5; 2 angry chunks become 5.
    """
    chunks: list[TaggedChunk] = []
    chunks.extend(
        _make_chunk(start=float(i), end=float(i) + 0.1, emotion="neutral", text=f"n{i}")
        for i in range(10)
    )
    chunks.extend(
        _make_chunk(start=100.0 + i, end=100.5 + i, emotion="angry", text=f"a{i}")
        for i in range(2)
    )

    result = rebalance_chunks(chunks, target_per_emotion=5)
    counts = Counter(c.emotion for c in result)

    assert counts["neutral"] == 5
    assert counts["angry"] == 5
    assert len(result) == 10


def test_rebalance_chunks_deterministic_seed() -> None:
    chunks = [
        _make_chunk(start=float(i), end=float(i) + 0.1, emotion="neutral", text=f"n{i}")
        for i in range(5)
    ]
    chunks.extend(
        _make_chunk(start=50.0 + i, end=50.5 + i, emotion="angry", text=f"a{i}")
        for i in range(2)
    )

    a = rebalance_chunks(chunks, random_seed=123)
    b = rebalance_chunks(chunks, random_seed=123)
    assert a == b

    c = rebalance_chunks(chunks, random_seed=999)
    # Different seed → almost certainly different shuffle / sampling.
    assert a != c or len(a) == 0


def test_rebalance_chunks_empty() -> None:
    assert rebalance_chunks([]) == []
    assert rebalance_chunks([], target_per_emotion=10) == []
