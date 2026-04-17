"""Unit tests for the ``scripts/voice_pack_analyze.py`` CLI orchestrator.

Everything is routed through injected ``transcribe_fn`` / ``diarize_fn``
hooks so none of these tests touch faster-whisper, pyannote, a GPU, or a
real audio file.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

# Import scripts/voice_pack_analyze.py by path — scripts/ is not a package.
_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "voice_pack_analyze.py"
)
_spec = importlib.util.spec_from_file_location("voice_pack_analyze", _SCRIPT_PATH)
voice_pack_analyze = importlib.util.module_from_spec(_spec)
sys.modules["voice_pack_analyze"] = voice_pack_analyze
assert _spec.loader is not None
_spec.loader.exec_module(voice_pack_analyze)  # type: ignore[union-attr]

from src.voice_pack.types import AsrSegment, DiarTurn  # noqa: E402


# --- Test fixtures ---------------------------------------------------------


def _fake_segments() -> list[AsrSegment]:
    """A small set of ASR segments with varied durations and confidence."""
    return [
        AsrSegment(start=0.0, end=3.0, text="Hello there friend.", confidence=0.9),
        AsrSegment(start=3.5, end=6.5, text="How are you today?", confidence=0.85),
        AsrSegment(start=7.0, end=10.0, text="I am doing well.", confidence=0.8),
        AsrSegment(start=11.0, end=14.0, text="That is good to hear.", confidence=0.7),
        AsrSegment(start=15.0, end=18.0, text="Indeed it is.", confidence=0.9),
    ]


def _fake_turns() -> list[DiarTurn]:
    """Two speakers, one dominant."""
    return [
        DiarTurn(start=0.0, end=10.5, speaker="SPEAKER_00"),
        DiarTurn(start=10.5, end=14.5, speaker="SPEAKER_01"),
        DiarTurn(start=14.5, end=18.5, speaker="SPEAKER_00"),
    ]


def _make_fakes() -> tuple[Any, Any]:
    def fake_transcribe(audio_path, **kwargs):  # noqa: ARG001 - signature compat
        return _fake_segments()

    def fake_diarize(audio_path, **kwargs):  # noqa: ARG001 - signature compat
        return _fake_turns()

    return fake_transcribe, fake_diarize


@pytest.fixture()
def fake_audio(tmp_path: Path) -> Path:
    audio = tmp_path / "sample_input.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")  # contents irrelevant for these tests
    return audio


# --- Tests -----------------------------------------------------------------


def test_analyze_writes_three_files(fake_audio: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    transcribe, diarize = _make_fakes()

    result = voice_pack_analyze.analyze(
        fake_audio,
        out,
        transcribe_fn=transcribe,
        diarize_fn=diarize,
    )

    assert (out / "transcripts.jsonl").exists()
    assert (out / "speakers.yaml").exists()
    assert (out / "report.md").exists()
    assert result.out_dir == out
    assert len(result.chunks) >= 1
    # At least one speaker survived the quality filter.
    assert len(result.speakers) >= 1


def test_analyze_jsonl_one_per_line_valid_json(
    fake_audio: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    transcribe, diarize = _make_fakes()

    voice_pack_analyze.analyze(
        fake_audio, out, transcribe_fn=transcribe, diarize_fn=diarize
    )

    raw = (out / "transcripts.jsonl").read_text(encoding="utf-8").splitlines()
    assert raw, "expected at least one chunk line"
    expected_keys = {"start", "end", "text", "speaker", "confidence"}
    for line in raw:
        obj = json.loads(line)
        assert isinstance(obj, dict)
        assert expected_keys.issubset(obj.keys())


def test_analyze_speakers_yaml_sorted_desc(
    fake_audio: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    transcribe, diarize = _make_fakes()

    voice_pack_analyze.analyze(
        fake_audio, out, transcribe_fn=transcribe, diarize_fn=diarize
    )

    data = yaml.safe_load((out / "speakers.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) >= 2
    totals = [entry["total_seconds"] for entry in data]
    assert totals == sorted(totals, reverse=True)
    # SPEAKER_00 is the dominant one in our fixture.
    assert data[0]["speaker"] == "SPEAKER_00"


def test_analyze_report_contains_tier_legend(
    fake_audio: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    transcribe, diarize = _make_fakes()

    voice_pack_analyze.analyze(
        fake_audio, out, transcribe_fn=transcribe, diarize_fn=diarize
    )

    report = (out / "report.md").read_text(encoding="utf-8")
    assert "full_lora" in report
    assert "few_shot" in report
    assert fake_audio.name in report
    assert "| Speaker | Total minutes | Chunks | Mean chunk (s) | Quality tier |" in report


def test_main_returns_zero_on_success(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    fake_audio: Path,
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"

    stub_result = voice_pack_analyze.AnalyzeResult(
        chunks=[object(), object(), object()],
        speakers=[object(), object()],
        out_dir=out,
    )

    def stub_analyze(*args: Any, **kwargs: Any):  # noqa: ARG001
        out.mkdir(parents=True, exist_ok=True)
        return stub_result

    monkeypatch.setattr(voice_pack_analyze, "analyze", stub_analyze)

    rc = voice_pack_analyze.main(
        ["--input", str(fake_audio), "--out", str(out)]
    )

    assert rc == 0
    captured = capfd.readouterr()
    assert "Analysis complete" in captured.out
    assert "3 chunks" in captured.out
    assert "2 speakers" in captured.out


def test_main_returns_one_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    fake_audio: Path,
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"

    def boom(*args: Any, **kwargs: Any):  # noqa: ARG001
        raise RuntimeError("diarization exploded")

    monkeypatch.setattr(voice_pack_analyze, "analyze", boom)

    rc = voice_pack_analyze.main(
        ["--input", str(fake_audio), "--out", str(out)]
    )

    assert rc == 1
    captured = capfd.readouterr()
    assert "diarization exploded" in captured.err
    assert "failed" in captured.err.lower()


def test_analyze_filters_apply(fake_audio: Path, tmp_path: Path) -> None:
    """A filter that drops everything should yield empty artefacts."""
    out = tmp_path / "out"
    transcribe, diarize = _make_fakes()

    result = voice_pack_analyze.analyze(
        fake_audio,
        out,
        transcribe_fn=transcribe,
        diarize_fn=diarize,
        # Unreachable confidence — every chunk gets dropped.
        min_confidence=2.0,
    )

    assert result.chunks == []
    assert result.speakers == []

    jsonl_text = (out / "transcripts.jsonl").read_text(encoding="utf-8")
    assert jsonl_text == ""

    yaml_data = yaml.safe_load((out / "speakers.yaml").read_text(encoding="utf-8"))
    # safe_dump of [] writes "[]\n" which safe_load parses back to [].
    assert yaml_data == [] or yaml_data is None
