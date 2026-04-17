"""Bucket ASR segments into per-speaker chunks and summarise for voice packs.

This module is the bridge between raw ASR + diarization output and the
downstream voice-cloning training stages. It does three things:

1. ``assign_speakers`` attributes each ASR segment to the diarization turn
   that overlaps it the most.
2. ``filter_quality`` drops chunks that would make bad training data
   (too short, too long, low confidence, empty text).
3. ``summarize_speakers`` aggregates per-speaker totals and picks a quality
   tier so the caller knows whether there is enough audio for a full LoRA,
   a reduced LoRA, a few-shot clone, or nothing usable at all.

Pure functions, no I/O, no external dependencies.
"""

from __future__ import annotations

from collections import defaultdict

from src.voice_pack.types import (
    AsrSegment,
    DiarTurn,
    SpeakerSummary,
    VoiceChunk,
    classify_quality_tier,
)


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Return the length of the overlap between intervals ``[a_start, a_end]``
    and ``[b_start, b_end]``. Zero if they do not overlap.

    Args:
        a_start: Start of interval A.
        a_end: End of interval A.
        b_start: Start of interval B.
        b_end: End of interval B.

    Returns:
        Overlap length in seconds, clamped to >= 0.
    """
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def assign_speakers(
    segments: list[AsrSegment],
    turns: list[DiarTurn],
) -> list[VoiceChunk]:
    """Attribute each ASR segment to the speaker whose diarization turn
    overlaps the segment the most.

    If no turn overlaps the segment at all, the segment is dropped
    (uncovered audio is not usable training data). Ties are broken by
    earliest turn start.

    Args:
        segments: ASR segments, in any order.
        turns: Diarization turns, in any order.

    Returns:
        VoiceChunks sorted by start ascending. Each chunk inherits its
        timing/text/confidence from the ASR segment and its speaker label
        from the best-overlapping diarization turn.
    """
    chunks: list[VoiceChunk] = []
    for seg in segments:
        best_overlap: float = 0.0
        best_turn: DiarTurn | None = None
        for turn in turns:
            ov = _overlap(seg.start, seg.end, turn.start, turn.end)
            if ov <= 0.0:
                continue
            if ov > best_overlap or (
                ov == best_overlap
                and best_turn is not None
                and turn.start < best_turn.start
            ):
                best_overlap = ov
                best_turn = turn
        if best_turn is None or best_overlap <= 0.0:
            continue
        chunks.append(
            VoiceChunk(
                start=seg.start,
                end=seg.end,
                text=seg.text,
                speaker=best_turn.speaker,
                confidence=seg.confidence,
            )
        )
    chunks.sort(key=lambda c: c.start)
    return chunks


def filter_quality(
    chunks: list[VoiceChunk],
    *,
    min_duration: float = 1.0,
    max_duration: float = 30.0,
    min_confidence: float = 0.3,
    require_text: bool = True,
) -> list[VoiceChunk]:
    """Drop chunks failing any of the quality gates. Preserve input order.

    Args:
        chunks: Candidate voice chunks.
        min_duration: Minimum chunk duration in seconds. Shorter chunks are
            dropped — too little audio for a cloning reference.
        max_duration: Maximum chunk duration in seconds. Longer chunks are
            dropped — usually an ASR runaway with unreliable alignment.
        min_confidence: Minimum ASR confidence. Lower-confidence chunks are
            dropped so we do not train on likely-wrong transcripts.
        require_text: If True, chunks whose text is empty or whitespace-only
            are dropped.

    Returns:
        The subset of ``chunks`` that pass every gate, in their original
        order.
    """
    kept: list[VoiceChunk] = []
    for chunk in chunks:
        if chunk.duration < min_duration:
            continue
        if chunk.duration > max_duration:
            continue
        if chunk.confidence < min_confidence:
            continue
        if require_text and not chunk.text.strip():
            continue
        kept.append(chunk)
    return kept


def summarize_speakers(chunks: list[VoiceChunk]) -> list[SpeakerSummary]:
    """Group chunks by speaker and compute per-speaker statistics.

    For each speaker the summary includes total seconds of clean audio,
    chunk count, mean chunk length, and the quality tier implied by the
    total duration. The speaker's own chunks are attached so downstream
    stages (ref-clip extraction, training-set export) can iterate over
    just that speaker's material.

    Args:
        chunks: Quality-filtered voice chunks.

    Returns:
        SpeakerSummary objects sorted by ``total_seconds`` descending so
        the primary narrator comes first. Empty input returns an empty
        list.
    """
    grouped: dict[str, list[VoiceChunk]] = defaultdict(list)
    for chunk in chunks:
        grouped[chunk.speaker].append(chunk)

    summaries: list[SpeakerSummary] = []
    for speaker, speaker_chunks in grouped.items():
        total_seconds = sum(c.duration for c in speaker_chunks)
        chunk_count = len(speaker_chunks)
        mean_chunk_seconds = total_seconds / chunk_count if chunk_count else 0.0
        summaries.append(
            SpeakerSummary(
                speaker=speaker,
                total_seconds=total_seconds,
                chunk_count=chunk_count,
                mean_chunk_seconds=mean_chunk_seconds,
                quality_tier=classify_quality_tier(total_seconds),
                chunks=list(speaker_chunks),
            )
        )

    summaries.sort(key=lambda s: s.total_seconds, reverse=True)
    return summaries
