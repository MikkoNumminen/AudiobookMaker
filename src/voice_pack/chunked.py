"""Chunked-analyze planning for long source audio.

Long inputs (≳ 6 min) tickle a native crash deep in faster-whisper /
pyannote (Windows STATUS_STACK_BUFFER_OVERRUN, exit code 0xC0000409)
that this module sidesteps by slicing the source into ≤ 5-min pieces
analysed independently.

Everything in this module is **pure** (no audio I/O, no ffmpeg). It
turns numbers into a chunk plan; the orchestrator (see
:mod:`src.voice_pack_chunked_subproc`) is responsible for actually
slicing and analysing.

Two kinds of chunks:

* **Hard chunks** — fixed-duration windows, the fallback when no
  silence boundary is reachable. Every long audio gets at least the
  hard plan as a baseline.
* **Silence-aware chunks** — the orchestrator finds silent regions
  near the planned boundaries and shifts each cut to the nearest one
  within a tolerance window so we never split a word in half.

The two are produced by the same :func:`plan_chunks` interface; the
``silence_intervals`` argument is optional and changes the boundary
selection. The data type is the same either way so the orchestrator
doesn't branch on the planner mode.

A 1-second overlap is added on either side of every interior boundary
(the very first and very last edges stay flush with the source).
This means each chunk's ASR sees a small lead-in / tail-out that
would otherwise have been clipped mid-syllable. Downstream merging
trims the overlap region's duplicate transcripts via
``OVERLAP_TRIM_SECONDS``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

#: Default ceiling per chunk in seconds. The empirical safe zone for
#: faster-whisper-large-v3 + pyannote 3.1 on a 12 GB CUDA card is
#: roughly 5 min before the native crash kicks in. Keep a margin.
DEFAULT_CHUNK_SECONDS: float = 300.0

#: Minimum chunk size. Avoids degenerate "one tiny tail chunk" plans
#: by absorbing leftovers shorter than this back into the previous
#: chunk. Below this length we'd waste a model load on too little
#: audio anyway.
DEFAULT_MIN_CHUNK_SECONDS: float = 30.0

#: How far the silence-aware planner is allowed to drift the cut from
#: its target. Wider window = fewer mid-word splits, but lets chunks
#: vary more in length. Tuned against typical audiobook silences (0.5
#: – 2 s gaps every couple of sentences).
DEFAULT_SILENCE_TOLERANCE_SECONDS: float = 20.0

#: Lead-in / tail-out overlap added at each interior boundary. Wide
#: enough to recover a clipped word; narrow enough that ASR's VAD
#: filter doesn't waste effort on the duplicated audio.
DEFAULT_OVERLAP_SECONDS: float = 1.0


@dataclass(frozen=True)
class AudioChunkPlan:
    """One planned slice of the source audio.

    ``index`` is the 0-based chunk position. ``start_global`` /
    ``end_global`` are seconds in the **source** timeline — apply this
    offset to every chunk-local timestamp before merging transcripts.

    ``slice_start`` / ``slice_end`` differ from start/end_global only
    when overlap is added: the slice covers a slightly wider span
    (``slice_start <= start_global``, ``slice_end >= end_global``) so
    the analyser sees the lead-in/tail-out audio. Reconciliation only
    keeps transcripts whose midpoint falls inside
    ``[start_global, end_global)`` to drop the duplicate overlap
    transcripts produced by the next chunk.
    """

    index: int
    start_global: float
    end_global: float
    slice_start: float
    slice_end: float

    @property
    def duration(self) -> float:
        """Effective duration owned by this chunk (excluding overlap)."""
        return max(0.0, self.end_global - self.start_global)

    @property
    def slice_duration(self) -> float:
        """Total slice duration including overlap (what ffmpeg cuts)."""
        return max(0.0, self.slice_end - self.slice_start)

    @property
    def overlap_lead(self) -> float:
        """Lead-in audio owned by this chunk's slice but not by it."""
        return max(0.0, self.start_global - self.slice_start)

    @property
    def overlap_tail(self) -> float:
        """Tail-out audio owned by this chunk's slice but not by it."""
        return max(0.0, self.slice_end - self.end_global)


def plan_chunks(
    total_seconds: float,
    *,
    chunk_seconds: float = DEFAULT_CHUNK_SECONDS,
    min_chunk_seconds: float = DEFAULT_MIN_CHUNK_SECONDS,
    silence_intervals: Sequence[tuple[float, float]] | None = None,
    silence_tolerance_seconds: float = DEFAULT_SILENCE_TOLERANCE_SECONDS,
    overlap_seconds: float = DEFAULT_OVERLAP_SECONDS,
) -> list[AudioChunkPlan]:
    """Compute a chunk plan for an audio of length ``total_seconds``.

    Args:
        total_seconds: Source duration. Negative or zero produces an
            empty plan.
        chunk_seconds: Target chunk length. Each chunk is at most this
            many seconds long, except possibly the last one.
        min_chunk_seconds: Smallest chunk we tolerate. The tail is
            absorbed into the previous chunk if it would be shorter.
        silence_intervals: Optional list of ``(start, end)`` silent
            spans in seconds. The planner snaps each interior cut to
            the centre of the nearest silence within
            ``silence_tolerance_seconds`` of the target. Pass ``None``
            (or an empty list) to use hard time-based cuts only.
        silence_tolerance_seconds: How far a silence centre can be
            from the target cut before we ignore it and use the hard
            target instead. Picked the closest in-window silence; ties
            broken by largest silence span.
        overlap_seconds: Lead-in / tail-out added at each interior
            boundary so words aren't clipped at the cut. The first /
            last edges stay flush with the source.

    Returns:
        A list of :class:`AudioChunkPlan` covering the whole source,
        in order. ``total_seconds <= chunk_seconds`` returns a single-
        chunk plan covering ``[0, total_seconds]`` with no overlap.

    Raises:
        ValueError: If ``chunk_seconds`` <= 0 or ``min_chunk_seconds``
            < 0 or ``min_chunk_seconds`` > ``chunk_seconds``.
    """
    if chunk_seconds <= 0:
        raise ValueError(f"chunk_seconds must be > 0, got {chunk_seconds}")
    if min_chunk_seconds < 0:
        raise ValueError(
            f"min_chunk_seconds must be >= 0, got {min_chunk_seconds}"
        )
    if min_chunk_seconds > chunk_seconds:
        raise ValueError(
            f"min_chunk_seconds ({min_chunk_seconds}) must be "
            f"<= chunk_seconds ({chunk_seconds})"
        )
    if total_seconds <= 0:
        return []

    # Single-chunk fast path.
    if total_seconds <= chunk_seconds:
        return [
            AudioChunkPlan(
                index=0,
                start_global=0.0,
                end_global=float(total_seconds),
                slice_start=0.0,
                slice_end=float(total_seconds),
            )
        ]

    # Step 1: target boundaries at uniform stride.
    targets: list[float] = []
    t = chunk_seconds
    while t < total_seconds:
        targets.append(t)
        t += chunk_seconds

    # Step 2: snap each target to nearest silence centre when possible.
    silences = _silence_centres(silence_intervals or [])
    snapped: list[float] = []
    for target in targets:
        cut = _snap_to_silence(
            target=target,
            silences=silences,
            tolerance=silence_tolerance_seconds,
        )
        snapped.append(cut)

    # Step 3: enforce monotonic boundaries (a snap could move a later
    # target past an earlier one). Re-clip in place.
    boundaries: list[float] = []
    for cut in snapped:
        if boundaries and cut <= boundaries[-1]:
            continue  # collapse — would produce an empty chunk
        boundaries.append(cut)

    # Step 4: absorb a too-short tail into the previous chunk.
    if total_seconds - (boundaries[-1] if boundaries else 0.0) < min_chunk_seconds:
        if boundaries:
            boundaries.pop()

    # Step 5: build chunk plans with overlap applied at interior edges.
    edges = [0.0] + boundaries + [float(total_seconds)]
    plans: list[AudioChunkPlan] = []
    for i in range(len(edges) - 1):
        start = edges[i]
        end = edges[i + 1]
        slice_start = start - (overlap_seconds if i > 0 else 0.0)
        slice_end = end + (overlap_seconds if i < len(edges) - 2 else 0.0)
        # Clamp to source bounds.
        slice_start = max(0.0, slice_start)
        slice_end = min(float(total_seconds), slice_end)
        plans.append(
            AudioChunkPlan(
                index=i,
                start_global=float(start),
                end_global=float(end),
                slice_start=float(slice_start),
                slice_end=float(slice_end),
            )
        )
    return plans


def _silence_centres(intervals: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Return ``[(centre, span)]`` for every silence interval, sorted."""
    out: list[tuple[float, float]] = []
    for s, e in intervals:
        if e <= s:
            continue
        out.append(((s + e) * 0.5, e - s))
    out.sort()
    return out


def _snap_to_silence(
    *,
    target: float,
    silences: list[tuple[float, float]],
    tolerance: float,
) -> float:
    """Return the nearest in-window silence centre, else ``target``.

    Ties (equal distance) broken by largest silence span — wider gaps
    are more reliable cut points than narrow ones.
    """
    if not silences or tolerance <= 0:
        return target
    best_cut = target
    best_dist = tolerance + 1.0  # outside window → no match yet
    best_span = 0.0
    for centre, span in silences:
        dist = abs(centre - target)
        if dist > tolerance:
            continue
        if dist < best_dist or (dist == best_dist and span > best_span):
            best_cut = centre
            best_dist = dist
            best_span = span
    return best_cut


def globalise_time(
    plan: AudioChunkPlan,
    chunk_local_seconds: float,
) -> float:
    """Convert a chunk-local timestamp into the source timeline.

    The analyse subprocess sees the slice (not the original audio), so
    its timestamps run from 0 to ``slice_duration``. To merge chunks
    we shift them back into the source timeline:

        global = slice_start + chunk_local

    Use this for every ``start`` and ``end`` field on every transcript
    before merging.
    """
    return plan.slice_start + max(0.0, float(chunk_local_seconds))


def chunk_owns_timestamp(
    plan: AudioChunkPlan,
    global_seconds: float,
) -> bool:
    """Return True iff ``global_seconds`` falls within this chunk's
    canonical span (excluding overlap).

    Used by the merger to drop transcripts that are duplicates of the
    next/previous chunk's overlap region. The interval is
    ``[start_global, end_global)`` — inclusive at the start, exclusive
    at the end — so each timestamp belongs to exactly one chunk even
    when boundaries land on an exact second.
    """
    return plan.start_global <= global_seconds < plan.end_global
