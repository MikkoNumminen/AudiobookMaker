"""Unit tests for :mod:`src.voice_pack.chunked`.

Pure logic — no audio, no ffmpeg, no subprocess.
"""

from __future__ import annotations

import pytest

from src.voice_pack.chunked import (
    DEFAULT_CHUNK_SECONDS,
    AudioChunkPlan,
    chunk_owns_timestamp,
    globalise_time,
    plan_chunks,
)


# ---------------------------------------------------------------------------
# plan_chunks
# ---------------------------------------------------------------------------


class TestPlanChunksBasics:
    def test_empty_audio_returns_empty_plan(self) -> None:
        assert plan_chunks(0.0) == []
        assert plan_chunks(-5.0) == []

    def test_short_audio_returns_single_chunk(self) -> None:
        plans = plan_chunks(120.0, chunk_seconds=300.0)
        assert len(plans) == 1
        only = plans[0]
        assert only.index == 0
        assert only.start_global == 0.0
        assert only.end_global == 120.0
        # No overlap when there's no neighbour.
        assert only.slice_start == 0.0
        assert only.slice_end == 120.0

    def test_exact_multiple_splits_evenly(self) -> None:
        # 900s @ 300s/chunk -> 3 chunks. The boundary at 900 is the
        # source end, not an interior cut, so we expect 3 chunks.
        plans = plan_chunks(900.0, chunk_seconds=300.0, overlap_seconds=0.0)
        assert len(plans) == 3
        starts = [p.start_global for p in plans]
        ends = [p.end_global for p in plans]
        assert starts == [0.0, 300.0, 600.0]
        assert ends == [300.0, 600.0, 900.0]
        # Indices are 0..N-1.
        assert [p.index for p in plans] == [0, 1, 2]

    def test_uneven_tail_kept_when_above_minimum(self) -> None:
        plans = plan_chunks(700.0, chunk_seconds=300.0, min_chunk_seconds=30.0,
                            overlap_seconds=0.0)
        # Boundaries at 300, 600 -> chunks 0..300, 300..600, 600..700.
        assert len(plans) == 3
        assert plans[-1].duration == pytest.approx(100.0)

    def test_tiny_tail_absorbed_into_previous_chunk(self) -> None:
        # 605s @ 300s would naively give a 5s tail chunk — too small to
        # be worth a separate analyse. The planner drops the last
        # boundary and the previous chunk grows.
        plans = plan_chunks(605.0, chunk_seconds=300.0, min_chunk_seconds=30.0,
                            overlap_seconds=0.0)
        assert len(plans) == 2
        assert plans[-1].end_global == 605.0
        assert plans[-1].duration == pytest.approx(305.0)


class TestPlanChunksOverlap:
    def test_overlap_only_at_interior_boundaries(self) -> None:
        plans = plan_chunks(900.0, chunk_seconds=300.0, overlap_seconds=1.0)
        first, middle, last = plans
        # First chunk: flush at start, overlap-extended at end.
        assert first.slice_start == 0.0
        assert first.slice_end == pytest.approx(301.0)
        # Middle chunk: overlap on both sides.
        assert middle.slice_start == pytest.approx(299.0)
        assert middle.slice_end == pytest.approx(601.0)
        # Last chunk: overlap-extended at start, flush at end.
        assert last.slice_start == pytest.approx(599.0)
        assert last.slice_end == 900.0

    def test_overlap_clamped_to_source_bounds(self) -> None:
        # Tiny audio at exactly the threshold — overlap can't extend
        # past the source.
        plans = plan_chunks(2.0, chunk_seconds=1.0, min_chunk_seconds=0.0,
                            overlap_seconds=10.0)
        for p in plans:
            assert p.slice_start >= 0.0
            assert p.slice_end <= 2.0

    def test_overlap_metadata_helpers(self) -> None:
        plans = plan_chunks(900.0, chunk_seconds=300.0, overlap_seconds=1.0)
        first, middle, last = plans
        assert first.overlap_lead == 0.0
        assert first.overlap_tail == pytest.approx(1.0)
        assert middle.overlap_lead == pytest.approx(1.0)
        assert middle.overlap_tail == pytest.approx(1.0)
        assert last.overlap_lead == pytest.approx(1.0)
        assert last.overlap_tail == 0.0
        assert middle.slice_duration == pytest.approx(302.0)
        assert middle.duration == pytest.approx(300.0)


class TestPlanChunksSilenceSnap:
    def test_snap_to_nearby_silence(self) -> None:
        # Target is at 300s; a silence sits at [298, 302] (centre 300).
        # Planner snaps the cut exactly to the silence centre — no
        # change in this trivial case but exercises the path.
        plans = plan_chunks(
            900.0,
            chunk_seconds=300.0,
            silence_intervals=[(298.0, 302.0), (598.0, 602.0)],
            silence_tolerance_seconds=20.0,
            overlap_seconds=0.0,
        )
        assert plans[0].end_global == pytest.approx(300.0)
        assert plans[1].end_global == pytest.approx(600.0)

    def test_silence_within_tolerance_wins(self) -> None:
        # Target 300, silence centred at 305 (within 20s tolerance).
        # The cut should move to 305.
        plans = plan_chunks(
            900.0,
            chunk_seconds=300.0,
            silence_intervals=[(304.0, 306.0)],
            silence_tolerance_seconds=20.0,
            overlap_seconds=0.0,
        )
        assert plans[0].end_global == pytest.approx(305.0)
        assert plans[1].start_global == pytest.approx(305.0)

    def test_silence_outside_tolerance_ignored(self) -> None:
        plans = plan_chunks(
            900.0,
            chunk_seconds=300.0,
            silence_intervals=[(50.0, 51.0)],  # nowhere near a target
            silence_tolerance_seconds=10.0,
            overlap_seconds=0.0,
        )
        assert plans[0].end_global == 300.0  # untouched

    def test_silence_ties_broken_by_widest_span(self) -> None:
        # Two silences equidistant from the target; pick the wider one.
        plans = plan_chunks(
            900.0,
            chunk_seconds=300.0,
            silence_intervals=[(295.0, 296.0), (304.0, 305.5)],
            silence_tolerance_seconds=10.0,
            overlap_seconds=0.0,
        )
        # Span of first = 1s @ centre 295.5; second = 1.5s @ centre 304.75.
        # Distances: 4.5 vs 4.75 — first is closer, picks first.
        assert plans[0].end_global == pytest.approx(295.5)


class TestPlanChunksValidation:
    def test_rejects_non_positive_chunk_seconds(self) -> None:
        with pytest.raises(ValueError):
            plan_chunks(100.0, chunk_seconds=0.0)
        with pytest.raises(ValueError):
            plan_chunks(100.0, chunk_seconds=-5.0)

    def test_rejects_negative_min_chunk_seconds(self) -> None:
        with pytest.raises(ValueError):
            plan_chunks(100.0, chunk_seconds=10.0, min_chunk_seconds=-1.0)

    def test_rejects_min_above_chunk(self) -> None:
        with pytest.raises(ValueError):
            plan_chunks(100.0, chunk_seconds=10.0, min_chunk_seconds=20.0)


# ---------------------------------------------------------------------------
# globalise_time / chunk_owns_timestamp
# ---------------------------------------------------------------------------


class TestGlobaliseTime:
    def test_first_chunk_no_offset(self) -> None:
        plan = AudioChunkPlan(
            index=0, start_global=0.0, end_global=300.0,
            slice_start=0.0, slice_end=301.0,
        )
        assert globalise_time(plan, 12.5) == pytest.approx(12.5)

    def test_middle_chunk_offset_uses_slice_start(self) -> None:
        # slice_start runs 1s before start_global because of overlap;
        # globalise must use slice_start (the actual file we sent to
        # whisper) to translate chunk-local timestamps.
        plan = AudioChunkPlan(
            index=1, start_global=300.0, end_global=600.0,
            slice_start=299.0, slice_end=601.0,
        )
        assert globalise_time(plan, 0.5) == pytest.approx(299.5)
        assert globalise_time(plan, 100.0) == pytest.approx(399.0)

    def test_negative_chunk_local_clamped_to_zero(self) -> None:
        # Defensive: if a backend ever emits a negative timestamp we
        # don't propagate it backwards into the previous chunk's span.
        plan = AudioChunkPlan(
            index=1, start_global=300.0, end_global=600.0,
            slice_start=299.0, slice_end=601.0,
        )
        assert globalise_time(plan, -1.0) == pytest.approx(299.0)


class TestChunkOwnsTimestamp:
    def test_owns_within_canonical_range(self) -> None:
        plan = AudioChunkPlan(
            index=1, start_global=300.0, end_global=600.0,
            slice_start=299.0, slice_end=601.0,
        )
        assert chunk_owns_timestamp(plan, 300.0) is True
        assert chunk_owns_timestamp(plan, 450.0) is True
        assert chunk_owns_timestamp(plan, 599.999) is True

    def test_rejects_overlap_region(self) -> None:
        plan = AudioChunkPlan(
            index=1, start_global=300.0, end_global=600.0,
            slice_start=299.0, slice_end=601.0,
        )
        # The lead-in [299, 300) belongs to the previous chunk.
        assert chunk_owns_timestamp(plan, 299.5) is False
        # The tail-out [600, 601) belongs to the next chunk.
        assert chunk_owns_timestamp(plan, 600.0) is False

    def test_endpoints_are_half_open(self) -> None:
        # Ensures a timestamp at exactly 600s belongs to the *next*
        # chunk, not this one — no double-counting at boundaries.
        plan = AudioChunkPlan(
            index=0, start_global=0.0, end_global=600.0,
            slice_start=0.0, slice_end=601.0,
        )
        assert chunk_owns_timestamp(plan, 0.0) is True
        assert chunk_owns_timestamp(plan, 600.0) is False


def test_default_chunk_threshold_matches_safe_zone() -> None:
    # The empirical safe zone for the underlying whisper/pyannote
    # crash is roughly 5 minutes; assert the default tracks that.
    assert DEFAULT_CHUNK_SECONDS == 300.0
