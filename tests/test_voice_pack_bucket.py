"""Unit tests for ``src.voice_pack.bucket``.

Covers the three public functions (``assign_speakers``, ``filter_quality``,
``summarize_speakers``) and an end-to-end pipeline test chaining all three.
"""

from __future__ import annotations

import pytest

from src.voice_pack.bucket import (
    assign_speakers,
    filter_quality,
    summarize_speakers,
)
from src.voice_pack.types import (
    AsrSegment,
    DiarTurn,
    SpeakerSummary,
    VoiceChunk,
    classify_quality_tier,
)


# ---------------------------------------------------------------------------
# assign_speakers
# ---------------------------------------------------------------------------


def test_assign_speakers_attributes_by_max_overlap() -> None:
    segs = [AsrSegment(0.5, 2.5, "hi", 0.9)]
    turns = [DiarTurn(0.0, 1.0, "S0"), DiarTurn(1.0, 3.0, "S1")]
    # overlap S0 = 0.5, S1 = 1.5 -> S1 wins
    out = assign_speakers(segs, turns)
    assert out == [VoiceChunk(0.5, 2.5, "hi", "S1", 0.9)]


def test_assign_speakers_multi_speaker_scenario() -> None:
    segs = [
        AsrSegment(0.0, 2.0, "alpha", 0.95),
        AsrSegment(2.0, 4.0, "bravo", 0.90),
        AsrSegment(4.0, 6.0, "charlie", 0.80),
    ]
    turns = [
        DiarTurn(0.0, 2.5, "S0"),
        DiarTurn(2.5, 6.0, "S1"),
    ]
    out = assign_speakers(segs, turns)
    assert [c.speaker for c in out] == ["S0", "S1", "S1"]
    assert [c.text for c in out] == ["alpha", "bravo", "charlie"]


def test_assign_speakers_drops_uncovered_segments() -> None:
    segs = [
        AsrSegment(0.0, 1.0, "covered", 0.9),
        AsrSegment(10.0, 11.0, "uncovered", 0.9),
    ]
    turns = [DiarTurn(0.0, 2.0, "S0")]
    out = assign_speakers(segs, turns)
    assert len(out) == 1
    assert out[0].text == "covered"


def test_assign_speakers_exact_boundary_segment_has_zero_overlap() -> None:
    # Segment sits exactly at the boundary between two turns - zero overlap
    # with either turn should mean the segment is dropped.
    segs = [AsrSegment(2.0, 2.0, "boundary", 0.9)]
    turns = [DiarTurn(0.0, 2.0, "S0"), DiarTurn(2.0, 4.0, "S1")]
    out = assign_speakers(segs, turns)
    assert out == []


def test_assign_speakers_segment_straddling_boundary_picks_larger_side() -> None:
    # Segment 1.0-3.0 across boundary 2.0: 1.0 overlap each side -> tie.
    # Tie broken by earliest turn start -> S0.
    segs = [AsrSegment(1.0, 3.0, "tied", 0.9)]
    turns = [DiarTurn(0.0, 2.0, "S0"), DiarTurn(2.0, 4.0, "S1")]
    out = assign_speakers(segs, turns)
    assert len(out) == 1
    assert out[0].speaker == "S0"


def test_assign_speakers_tie_broken_by_earliest_turn_start() -> None:
    # Both turns overlap the segment by the same amount (1.0 s).
    # Earliest turn wins regardless of input order.
    segs = [AsrSegment(0.0, 2.0, "t", 0.9)]
    turns = [
        DiarTurn(1.0, 3.0, "later"),
        DiarTurn(-1.0, 1.0, "earlier"),
    ]
    out = assign_speakers(segs, turns)
    assert len(out) == 1
    assert out[0].speaker == "earlier"


def test_assign_speakers_sorts_output_by_start() -> None:
    segs = [
        AsrSegment(5.0, 6.0, "late", 0.9),
        AsrSegment(0.0, 1.0, "early", 0.9),
        AsrSegment(2.0, 3.0, "mid", 0.9),
    ]
    turns = [DiarTurn(0.0, 10.0, "S0")]
    out = assign_speakers(segs, turns)
    assert [c.text for c in out] == ["early", "mid", "late"]


def test_assign_speakers_empty_inputs() -> None:
    assert assign_speakers([], []) == []
    assert assign_speakers([], [DiarTurn(0.0, 1.0, "S0")]) == []
    assert assign_speakers([AsrSegment(0.0, 1.0, "hi", 0.9)], []) == []


# ---------------------------------------------------------------------------
# filter_quality
# ---------------------------------------------------------------------------


def _chunk(
    start: float = 0.0,
    end: float = 2.0,
    text: str = "hello",
    speaker: str = "S0",
    confidence: float = 0.9,
) -> VoiceChunk:
    return VoiceChunk(start, end, text, speaker, confidence)


def test_filter_quality_all_pass() -> None:
    chunks = [_chunk(0.0, 2.0), _chunk(2.0, 5.0)]
    assert filter_quality(chunks) == chunks


def test_filter_quality_drops_too_short() -> None:
    chunks = [_chunk(0.0, 0.5), _chunk(1.0, 3.0)]
    out = filter_quality(chunks, min_duration=1.0)
    assert len(out) == 1
    assert out[0].start == 1.0


def test_filter_quality_drops_too_long() -> None:
    chunks = [_chunk(0.0, 2.0), _chunk(0.0, 60.0)]
    out = filter_quality(chunks, max_duration=30.0)
    assert len(out) == 1
    assert out[0].end == 2.0


def test_filter_quality_drops_low_confidence() -> None:
    chunks = [_chunk(confidence=0.2), _chunk(confidence=0.9)]
    out = filter_quality(chunks, min_confidence=0.3)
    assert len(out) == 1
    assert out[0].confidence == 0.9


def test_filter_quality_drops_empty_text() -> None:
    chunks = [_chunk(text=""), _chunk(text="   "), _chunk(text="real")]
    out = filter_quality(chunks, require_text=True)
    assert len(out) == 1
    assert out[0].text == "real"


def test_filter_quality_can_allow_empty_text() -> None:
    chunks = [_chunk(text=""), _chunk(text="real")]
    out = filter_quality(chunks, require_text=False)
    assert len(out) == 2


def test_filter_quality_preserves_order() -> None:
    chunks = [
        _chunk(0.0, 2.0, text="a"),
        _chunk(2.0, 4.0, text="b"),
        _chunk(4.0, 6.0, text="c"),
    ]
    out = filter_quality(chunks)
    assert [c.text for c in out] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# summarize_speakers
# ---------------------------------------------------------------------------


def test_summarize_speakers_empty_input_returns_empty_list() -> None:
    assert summarize_speakers([]) == []


def test_summarize_speakers_orders_biggest_first() -> None:
    chunks = [
        _chunk(0.0, 1.0, speaker="small"),
        _chunk(0.0, 10.0, speaker="big"),
        _chunk(0.0, 5.0, speaker="mid"),
    ]
    out = summarize_speakers(chunks)
    assert [s.speaker for s in out] == ["big", "mid", "small"]


def test_summarize_speakers_computes_mean() -> None:
    chunks = [
        _chunk(0.0, 2.0, speaker="S0"),
        _chunk(2.0, 6.0, speaker="S0"),
    ]
    out = summarize_speakers(chunks)
    assert len(out) == 1
    s = out[0]
    assert s.chunk_count == 2
    assert s.total_seconds == pytest.approx(6.0)
    assert s.mean_chunk_seconds == pytest.approx(3.0)


def test_summarize_speakers_tier_classification_matches_helper() -> None:
    chunks = [_chunk(0.0, 120.0, speaker="S0")]
    out = summarize_speakers(chunks)
    assert out[0].quality_tier == classify_quality_tier(120.0)


def test_summarize_speakers_attaches_chunk_lists() -> None:
    chunks = [
        _chunk(0.0, 2.0, speaker="S0", text="a"),
        _chunk(2.0, 4.0, speaker="S1", text="b"),
        _chunk(4.0, 6.0, speaker="S0", text="c"),
    ]
    out = summarize_speakers(chunks)
    by_speaker = {s.speaker: s for s in out}
    assert [c.text for c in by_speaker["S0"].chunks] == ["a", "c"]
    assert [c.text for c in by_speaker["S1"].chunks] == ["b"]


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def test_pipeline_assign_filter_summarize_end_to_end() -> None:
    """Realistic mini scenario chaining all three functions.

    Two speakers trade lines in a short dialogue. One ASR segment lands in
    uncovered audio (dropped by ``assign_speakers``); another is too short
    (dropped by ``filter_quality``); another has empty text (also dropped);
    the rest survive. Final summary must put the heavier speaker first.
    """
    segments = [
        # S0: decent chunk
        AsrSegment(0.0, 4.0, "Once upon a time", 0.95),
        # S1: decent chunk
        AsrSegment(4.0, 7.0, "said the fox", 0.90),
        # S0: another decent chunk - longest for S0
        AsrSegment(7.0, 15.0, "and the story went on for a while", 0.92),
        # Uncovered region - will be dropped by assign_speakers
        AsrSegment(100.0, 102.0, "off the reservation", 0.95),
        # Too short - will pass assign, fail filter
        AsrSegment(15.0, 15.3, "hm", 0.9),
        # Empty text - will pass assign, fail filter
        AsrSegment(15.3, 17.0, "   ", 0.9),
    ]
    turns = [
        DiarTurn(0.0, 4.0, "S0"),
        DiarTurn(4.0, 7.0, "S1"),
        DiarTurn(7.0, 20.0, "S0"),
    ]

    assigned = assign_speakers(segments, turns)
    # Uncovered segment dropped; others attributed.
    assert len(assigned) == 5
    assert all(c.speaker in {"S0", "S1"} for c in assigned)

    cleaned = filter_quality(assigned)
    # Short and empty chunks gone.
    assert len(cleaned) == 3
    assert all(c.duration >= 1.0 for c in cleaned)
    assert all(c.text.strip() for c in cleaned)

    summary = summarize_speakers(cleaned)
    assert isinstance(summary[0], SpeakerSummary)
    assert [s.speaker for s in summary] == ["S0", "S1"]
    s0 = summary[0]
    s1 = summary[1]
    assert s0.chunk_count == 2
    assert s1.chunk_count == 1
    assert s0.total_seconds == pytest.approx(12.0)
    assert s1.total_seconds == pytest.approx(3.0)
    assert s0.mean_chunk_seconds == pytest.approx(6.0)
    assert s1.mean_chunk_seconds == pytest.approx(3.0)
    # Per-speaker chunk lists carry through.
    assert [c.text for c in s0.chunks] == [
        "Once upon a time",
        "and the story went on for a while",
    ]
    assert [c.text for c in s1.chunks] == ["said the fox"]
