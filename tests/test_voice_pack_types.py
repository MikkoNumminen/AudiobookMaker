"""Unit tests for voice pack shared data types + tier classifier."""

from __future__ import annotations

import pytest

from src.voice_pack.types import (
    AsrSegment,
    DiarTurn,
    SpeakerSummary,
    VoiceChunk,
    classify_quality_tier,
)


class TestAsrSegment:
    def test_duration(self) -> None:
        seg = AsrSegment(start=1.0, end=3.5, text="hello", confidence=0.9)
        assert seg.duration == pytest.approx(2.5)

    def test_duration_negative_clamped(self) -> None:
        seg = AsrSegment(start=2.0, end=1.0, text="", confidence=1.0)
        assert seg.duration == 0.0

    def test_default_confidence(self) -> None:
        seg = AsrSegment(start=0.0, end=1.0, text="x")
        assert seg.confidence == 1.0

    def test_to_dict(self) -> None:
        seg = AsrSegment(start=0.0, end=1.0, text="x", confidence=0.5)
        d = seg.to_dict()
        assert d == {"start": 0.0, "end": 1.0, "text": "x", "confidence": 0.5}


class TestDiarTurn:
    def test_duration(self) -> None:
        turn = DiarTurn(start=0.0, end=4.0, speaker="SPEAKER_00")
        assert turn.duration == 4.0

    def test_to_dict(self) -> None:
        turn = DiarTurn(start=0.0, end=1.0, speaker="S0")
        assert turn.to_dict() == {"start": 0.0, "end": 1.0, "speaker": "S0"}


class TestVoiceChunk:
    def test_fields(self) -> None:
        chunk = VoiceChunk(
            start=1.0, end=3.0, text="hi", speaker="S0", confidence=0.8
        )
        assert chunk.duration == 2.0
        assert chunk.text == "hi"


class TestSpeakerSummary:
    def test_to_dict_without_chunks(self) -> None:
        s = SpeakerSummary(
            speaker="SPEAKER_00",
            total_seconds=1800.0,
            chunk_count=600,
            mean_chunk_seconds=3.0,
            quality_tier="full_lora",
        )
        d = s.to_dict()
        assert d["speaker"] == "SPEAKER_00"
        assert d["total_minutes"] == 30.0
        assert d["quality_tier"] == "full_lora"
        assert "chunks" not in d

    def test_to_dict_with_chunks(self) -> None:
        c = VoiceChunk(start=0.0, end=1.0, text="a", speaker="S0", confidence=1.0)
        s = SpeakerSummary(
            speaker="S0",
            total_seconds=1.0,
            chunk_count=1,
            mean_chunk_seconds=1.0,
            quality_tier="skip",
            chunks=[c],
        )
        d = s.to_dict(include_chunks=True)
        assert len(d["chunks"]) == 1
        assert d["chunks"][0]["text"] == "a"


class TestClassifyQualityTier:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0.0, "skip"),
            (30.0, "skip"),
            (59.9, "skip"),
            (60.0, "few_shot"),
            (5 * 60, "few_shot"),
            (10 * 60 - 0.1, "few_shot"),
            (10 * 60, "reduced_lora"),
            (20 * 60, "reduced_lora"),
            (30 * 60 - 0.1, "reduced_lora"),
            (30 * 60, "full_lora"),
            (3600.0, "full_lora"),
            (10 * 3600.0, "full_lora"),
        ],
    )
    def test_tier_boundaries(self, seconds: float, expected: str) -> None:
        assert classify_quality_tier(seconds) == expected
