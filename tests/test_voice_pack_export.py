"""Tests for scripts/voice_pack_export.py.

These tests stay hermetic — no torch, no audio libraries, no GPU. The
audio slicer inside :func:`export_dataset` is reached indirectly through
the real function; to avoid needing a real audio file we pass a
zero-length ``.wav`` and exercise the branches that don't actually slice
(no chunks) as well as the branches that do (via a recording slicer
injected into the export call). The CLI and thin wrapper together are the
unit under test.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# scripts/ isn't a package — load the module by path the same way the
# other voice-pack CLI tests do.
_spec = importlib.util.spec_from_file_location(
    "voice_pack_export",
    Path(__file__).resolve().parents[1] / "scripts" / "voice_pack_export.py",
)
voice_pack_export = importlib.util.module_from_spec(_spec)
sys.modules["voice_pack_export"] = voice_pack_export
assert _spec.loader is not None
_spec.loader.exec_module(voice_pack_export)

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.voice_pack.types import VoiceChunk  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_transcripts(tmp_path: Path, chunks: list[VoiceChunk]) -> Path:
    path = tmp_path / "transcripts.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for chunk in chunks:
            fh.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
    return path


def _make_chunks() -> list[VoiceChunk]:
    return [
        VoiceChunk(
            start=0.0,
            end=3.0,
            text="First line from speaker zero.",
            speaker="SPEAKER_00",
            confidence=0.95,
        ),
        VoiceChunk(
            start=3.5,
            end=6.2,
            text="Second line from speaker zero.",
            speaker="SPEAKER_00",
            confidence=0.92,
        ),
        VoiceChunk(
            start=6.5,
            end=9.0,
            text="A single line from speaker one.",
            speaker="SPEAKER_01",
            confidence=0.88,
        ),
    ]


def _noop_slicer(src, start, end, out_path, sample_rate_hz):
    """Writes a 44-byte empty WAV stub so export_dataset treats the slice
    as produced without actually touching pydub/ffmpeg."""
    Path(out_path).write_bytes(b"RIFF" + b"\x00" * 40)


# ---------------------------------------------------------------------------
# _iter_transcripts
# ---------------------------------------------------------------------------


def test_iter_transcripts_parses_valid_rows(tmp_path: Path) -> None:
    path = _write_transcripts(tmp_path, _make_chunks())
    chunks = voice_pack_export._iter_transcripts(path)
    assert len(chunks) == 3
    assert chunks[0].speaker == "SPEAKER_00"
    assert chunks[0].text.startswith("First")
    assert chunks[-1].speaker == "SPEAKER_01"


def test_iter_transcripts_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps(_make_chunks()[0].to_dict()) + "\n\n   \n",
        encoding="utf-8",
    )
    chunks = voice_pack_export._iter_transcripts(path)
    assert len(chunks) == 1


def test_iter_transcripts_raises_on_bad_row(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line_no|could not parse|t.jsonl"):
        voice_pack_export._iter_transcripts(path)


def test_iter_transcripts_raises_on_missing_field(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps({"start": 0, "end": 1, "text": "x"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        voice_pack_export._iter_transcripts(path)


# ---------------------------------------------------------------------------
# export_for_speaker
# ---------------------------------------------------------------------------


def test_export_for_speaker_filters_and_writes_manifest(tmp_path: Path) -> None:
    transcripts = _write_transcripts(tmp_path, _make_chunks())
    source = tmp_path / "src.wav"
    source.write_bytes(b"RIFF" + b"\x00" * 40)
    out_dir = tmp_path / "ds"

    # Monkey-patch export_dataset's default slicer by injecting our own via
    # its audio_slicer kwarg. The wrapper doesn't expose that today, so we
    # patch it in-place for the duration of the test.
    orig_export = voice_pack_export.export_dataset

    def _patched_export(*args, **kwargs):
        kwargs.setdefault("audio_slicer", _noop_slicer)
        return orig_export(*args, **kwargs)

    voice_pack_export.export_dataset = _patched_export
    try:
        manifest = voice_pack_export.export_for_speaker(
            transcripts_path=transcripts,
            source_audio_path=source,
            speaker="SPEAKER_00",
            out_dir=out_dir,
        )
    finally:
        voice_pack_export.export_dataset = orig_export

    assert manifest.speaker == "SPEAKER_00"
    assert len(manifest.clips) == 2  # only SPEAKER_00's two lines
    assert (out_dir / "manifest.json").exists()
    assert all(c.emotion == "neutral" for c in manifest.clips)


def test_export_for_speaker_unknown_speaker_raises(tmp_path: Path) -> None:
    transcripts = _write_transcripts(tmp_path, _make_chunks())
    source = tmp_path / "src.wav"
    source.write_bytes(b"RIFF")

    with pytest.raises(ValueError) as excinfo:
        voice_pack_export.export_for_speaker(
            transcripts_path=transcripts,
            source_audio_path=source,
            speaker="SPEAKER_42",
            out_dir=tmp_path / "ds",
        )
    msg = str(excinfo.value)
    assert "SPEAKER_42" in msg
    assert "SPEAKER_00" in msg  # lists actually-present speakers


def test_export_for_speaker_bad_emotion_raises(tmp_path: Path) -> None:
    transcripts = _write_transcripts(tmp_path, _make_chunks())
    source = tmp_path / "src.wav"
    source.write_bytes(b"RIFF")

    with pytest.raises(ValueError, match="emotion"):
        voice_pack_export.export_for_speaker(
            transcripts_path=transcripts,
            source_audio_path=source,
            speaker="SPEAKER_00",
            out_dir=tmp_path / "ds",
            emotion_label="ecstatic",
        )


def test_export_for_speaker_missing_transcripts_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="transcripts"):
        voice_pack_export.export_for_speaker(
            transcripts_path=tmp_path / "nope.jsonl",
            source_audio_path=tmp_path / "src.wav",
            speaker="SPEAKER_00",
            out_dir=tmp_path / "ds",
        )


def test_export_for_speaker_missing_source_raises(tmp_path: Path) -> None:
    transcripts = _write_transcripts(tmp_path, _make_chunks())
    with pytest.raises(FileNotFoundError, match="source"):
        voice_pack_export.export_for_speaker(
            transcripts_path=transcripts,
            source_audio_path=tmp_path / "nope.wav",
            speaker="SPEAKER_00",
            out_dir=tmp_path / "ds",
        )


# ---------------------------------------------------------------------------
# CLI main()
# ---------------------------------------------------------------------------


def test_main_happy_path(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    transcripts = _write_transcripts(tmp_path, _make_chunks())
    source = tmp_path / "src.wav"
    source.write_bytes(b"RIFF" + b"\x00" * 40)
    out_dir = tmp_path / "ds"

    orig_export = voice_pack_export.export_dataset

    def _patched_export(*args, **kwargs):
        kwargs.setdefault("audio_slicer", _noop_slicer)
        return orig_export(*args, **kwargs)

    voice_pack_export.export_dataset = _patched_export
    try:
        rc = voice_pack_export.main(
            [
                "--transcripts",
                str(transcripts),
                "--source",
                str(source),
                "--speaker",
                "SPEAKER_00",
                "--out",
                str(out_dir),
            ]
        )
    finally:
        voice_pack_export.export_dataset = orig_export

    assert rc == 0
    captured = capfd.readouterr()
    assert "Exported 2 clips" in captured.out
    assert "SPEAKER_00" in captured.out
    assert (out_dir / "manifest.json").exists()


def test_main_bad_speaker_returns_one(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    transcripts = _write_transcripts(tmp_path, _make_chunks())
    source = tmp_path / "src.wav"
    source.write_bytes(b"RIFF")

    rc = voice_pack_export.main(
        [
            "--transcripts",
            str(transcripts),
            "--source",
            str(source),
            "--speaker",
            "SPEAKER_99",
            "--out",
            str(tmp_path / "ds"),
        ]
    )
    assert rc == 1
    captured = capfd.readouterr()
    assert "SPEAKER_99" in captured.err


def test_main_missing_transcripts_returns_one(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    rc = voice_pack_export.main(
        [
            "--transcripts",
            str(tmp_path / "nope.jsonl"),
            "--source",
            str(tmp_path / "src.wav"),
            "--speaker",
            "SPEAKER_00",
            "--out",
            str(tmp_path / "ds"),
        ]
    )
    assert rc == 1
    captured = capfd.readouterr()
    assert "transcripts" in captured.err.lower()
