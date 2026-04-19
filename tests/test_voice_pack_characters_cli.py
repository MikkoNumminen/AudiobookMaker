"""Tests for scripts/voice_pack_characters.py.

These stay hermetic — no torch, no chatterbox, no audio libs. The slicer
and embedder are both injected as fakes so the CLI surface (file I/O,
argparse, flag plumbing) is all that's actually exercised.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

# scripts/ isn't a package — load the module by path.
_spec = importlib.util.spec_from_file_location(
    "voice_pack_characters",
    Path(__file__).resolve().parents[1] / "scripts" / "voice_pack_characters.py",
)
voice_pack_characters = importlib.util.module_from_spec(_spec)
sys.modules["voice_pack_characters"] = voice_pack_characters
assert _spec.loader is not None
_spec.loader.exec_module(voice_pack_characters)

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


def _two_character_chunks() -> list[VoiceChunk]:
    """10 chunks, first 5 are narrator (dir A), next 5 are villain (dir B)."""
    chunks: list[VoiceChunk] = []
    for i in range(10):
        chunks.append(
            VoiceChunk(
                start=float(i * 5),
                end=float(i * 5 + 4),
                text=f"line {i}",
                speaker="SPEAKER_00",
                confidence=0.95,
            )
        )
    return chunks


def _deterministic_slicer(
    src: Path, start_s: float, end_s: float
) -> tuple[object, int]:
    """Returns the chunk start-time as the 'waveform', embedder uses it."""
    return np.array([start_s], dtype=np.float32), 16000


def _two_direction_embedder(wav: object, sample_rate_hz: int) -> object:
    """First half of the timeline (< 25 s) gets embedding [1,0]; rest gets [0,1]."""
    start = float(np.asarray(wav).reshape(-1)[0])
    if start < 25.0:
        return np.array([1.0, 0.0])
    return np.array([0.0, 1.0])


# ---------------------------------------------------------------------------
# _iter_transcripts
# ---------------------------------------------------------------------------


def test_iter_transcripts_parses_character_field(tmp_path: Path) -> None:
    chunks = [
        VoiceChunk(
            start=0.0, end=1.0, text="hi", speaker="S0", confidence=1.0,
            character="CHAR_A",
        )
    ]
    path = _write_transcripts(tmp_path, chunks)
    loaded = voice_pack_characters._iter_transcripts(path)
    assert loaded[0].character == "CHAR_A"


def test_iter_transcripts_accepts_missing_character(tmp_path: Path) -> None:
    # Backward-compat: old transcripts without a character field.
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps(
            {
                "start": 0.0, "end": 1.0, "text": "hi",
                "speaker": "S0", "confidence": 1.0,
            }
        ) + "\n",
        encoding="utf-8",
    )
    loaded = voice_pack_characters._iter_transcripts(path)
    assert loaded[0].character is None


# ---------------------------------------------------------------------------
# cluster_transcripts — end-to-end with fake slicer/embedder
# ---------------------------------------------------------------------------


def test_cluster_transcripts_writes_artefacts(tmp_path: Path) -> None:
    transcripts = _write_transcripts(tmp_path, _two_character_chunks())
    source = tmp_path / "src.wav"
    source.write_bytes(b"RIFF" + b"\x00" * 40)
    out_dir = tmp_path / "chars"

    result = voice_pack_characters.cluster_transcripts(
        transcripts_path=transcripts,
        source_audio_path=source,
        out_dir=out_dir,
        distance_threshold=0.2,
        min_character_seconds=5.0,
        min_character_chunks=3,
        audio_slicer=_deterministic_slicer,
        embedder=_two_direction_embedder,
    )

    # Two clusters discovered: first 5 chunks vs last 5 chunks.
    chars = {c.character for c in result.chunks}
    assert chars == {"CHAR_A", "CHAR_B"}
    assert len(result.summaries) == 2

    # Artefacts written.
    assert (out_dir / "transcripts_with_characters.jsonl").exists()
    assert (out_dir / "characters.yaml").exists()
    assert (out_dir / "characters_report.md").exists()

    # JSONL is one VoiceChunk per line with character populated.
    lines = (out_dir / "transcripts_with_characters.jsonl").read_text(
        encoding="utf-8"
    ).strip().splitlines()
    assert len(lines) == 10
    for line in lines:
        payload = json.loads(line)
        assert payload["character"] in {"CHAR_A", "CHAR_B"}


def test_cluster_transcripts_max_chunks_per_speaker_subsamples(
    tmp_path: Path,
) -> None:
    transcripts = _write_transcripts(tmp_path, _two_character_chunks())
    source = tmp_path / "src.wav"
    source.write_bytes(b"RIFF" + b"\x00" * 40)

    calls: list[float] = []

    def counting_slicer(src, start_s, end_s):
        calls.append(start_s)
        return _deterministic_slicer(src, start_s, end_s)

    voice_pack_characters.cluster_transcripts(
        transcripts_path=transcripts,
        source_audio_path=source,
        out_dir=tmp_path / "chars",
        distance_threshold=0.2,
        min_character_seconds=5.0,
        min_character_chunks=2,
        max_chunks_per_speaker=4,
        audio_slicer=counting_slicer,
        embedder=_two_direction_embedder,
    )
    # We asked for at most 4 chunks embedded; the slicer was called only
    # that many times.
    assert len(calls) == 4


def test_cluster_transcripts_missing_transcripts_raises(tmp_path: Path) -> None:
    source = tmp_path / "src.wav"
    source.write_bytes(b"RIFF")
    with pytest.raises(FileNotFoundError, match="transcripts"):
        voice_pack_characters.cluster_transcripts(
            transcripts_path=tmp_path / "nope.jsonl",
            source_audio_path=source,
            out_dir=tmp_path / "chars",
            audio_slicer=_deterministic_slicer,
            embedder=_two_direction_embedder,
        )


def test_cluster_transcripts_missing_source_raises(tmp_path: Path) -> None:
    transcripts = _write_transcripts(tmp_path, _two_character_chunks())
    with pytest.raises(FileNotFoundError, match="source"):
        voice_pack_characters.cluster_transcripts(
            transcripts_path=transcripts,
            source_audio_path=tmp_path / "nope.wav",
            out_dir=tmp_path / "chars",
            audio_slicer=_deterministic_slicer,
            embedder=_two_direction_embedder,
        )


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_happy_path(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    transcripts = _write_transcripts(tmp_path, _two_character_chunks())
    source = tmp_path / "src.wav"
    source.write_bytes(b"RIFF" + b"\x00" * 40)
    out_dir = tmp_path / "chars"

    # Inject fake slicer/embedder by replacing the defaults.
    original_slicer = voice_pack_characters._default_slicer
    original_embedder = voice_pack_characters._default_embedder
    voice_pack_characters._default_slicer = lambda: _deterministic_slicer
    voice_pack_characters._default_embedder = lambda: _two_direction_embedder
    try:
        rc = voice_pack_characters.main(
            [
                "--transcripts", str(transcripts),
                "--source", str(source),
                "--out", str(out_dir),
                "--distance-threshold", "0.2",
                "--min-character-seconds", "5",
                "--min-character-chunks", "3",
            ]
        )
    finally:
        voice_pack_characters._default_slicer = original_slicer
        voice_pack_characters._default_embedder = original_embedder

    assert rc == 0
    captured = capfd.readouterr()
    assert "Clustered" in captured.out
    assert (out_dir / "transcripts_with_characters.jsonl").exists()


def test_main_missing_transcripts_returns_one(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    rc = voice_pack_characters.main(
        [
            "--transcripts", str(tmp_path / "nope.jsonl"),
            "--source", str(tmp_path / "src.wav"),
            "--out", str(tmp_path / "chars"),
        ]
    )
    assert rc == 1
    captured = capfd.readouterr()
    assert "transcripts" in captured.err.lower()
