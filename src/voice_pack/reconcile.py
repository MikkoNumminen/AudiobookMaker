"""Cross-chunk speaker reconciliation.

When the source is sliced into N chunks (see :mod:`src.voice_pack.chunked`)
each chunk is analysed independently. Diarization labels in different
chunks are unrelated: ``SPEAKER_00`` in chunk 0 is not the same person
as ``SPEAKER_00`` in chunk 1.

This module reconciles them. Inputs are voice embeddings (one per
``(chunk_index, local_speaker)`` pair) and a similarity threshold; the
output is a mapping from local speaker labels back to global ones
(``SPEAKER_GLOBAL_00`` etc.) and a list of ambiguous merges the
operator should look at.

The algorithm is union-find over a thresholded cosine-similarity
graph, exactly the same shape as the character clusterer in
:mod:`src.voice_pack.characters` but applied across chunks instead of
within one. Two pairs of thresholds:

* ``hard_merge_threshold`` (default 0.75) — pairs above this are
  union-merged, no questions asked.
* ``ambiguous_threshold`` (default 0.65) — pairs in the half-open
  band ``[ambiguous, hard)`` are flagged for human review but **not**
  merged. The report carries them so the operator can decide.

Pure logic — no audio I/O. Tests pass synthetic embedding matrices.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

#: Cosine similarity at or above which two local speakers are merged.
#: Tuned for the Chatterbox voice encoder which produces unit-norm
#: 256-d embeddings; same-speaker pairs typically score 0.85-0.95,
#: cross-speaker 0.30-0.55.
DEFAULT_HARD_MERGE_THRESHOLD: float = 0.75

#: Cosine similarity at or above which a pair is flagged ambiguous.
#: Below this, two clusters are treated as definitely-different and
#: kept apart.
DEFAULT_AMBIGUOUS_THRESHOLD: float = 0.65


@dataclass(frozen=True)
class LocalSpeakerKey:
    """Identifies one speaker label inside one chunk.

    The orchestrator builds these from ``(chunk_index, speaker_id)``
    where ``speaker_id`` is the diarizer-assigned local label
    (``SPEAKER_00`` etc., before reconciliation).
    """

    chunk_index: int
    local_speaker: str


@dataclass(frozen=True)
class AmbiguousMerge:
    """One cross-chunk pair whose similarity sits in the grey band.

    Carried in :class:`ReconciliationResult.ambiguous_pairs` so the
    operator can decide whether to merge manually. The pair is
    **not** merged automatically.
    """

    a: LocalSpeakerKey
    b: LocalSpeakerKey
    similarity: float


@dataclass
class ReconciliationResult:
    """Output of :func:`reconcile_speakers`.

    ``label_map`` is the workhorse: every input ``LocalSpeakerKey``
    maps to a global label like ``SPEAKER_GLOBAL_00``. Use it to
    rewrite the per-chunk transcripts before merging into one
    transcripts.jsonl.

    ``cluster_members`` is the inverse view — for each global label,
    the local keys that fold into it. Useful for the report.

    ``ambiguous_pairs`` is the grey-band log; ``cluster_durations``
    captures total seconds per global cluster so the caller can rank
    clusters by size.
    """

    label_map: dict[LocalSpeakerKey, str] = field(default_factory=dict)
    cluster_members: dict[str, list[LocalSpeakerKey]] = field(default_factory=dict)
    cluster_durations: dict[str, float] = field(default_factory=dict)
    ambiguous_pairs: list[AmbiguousMerge] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _normalize_rows(matrix):
    """L2-normalise each row of a 2-D ``np.ndarray``. Idempotent."""
    import numpy as np  # type: ignore

    if matrix.ndim != 2:
        raise ValueError(f"expected 2-D embeddings, got shape {matrix.shape}")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return matrix / norms


def cosine_similarity_matrix(embeddings) -> "object":
    """Return the ``N x N`` cosine-similarity matrix for ``embeddings``.

    Diagonal is 1.0 (within rounding). Embeddings are normalised
    before the matmul so callers don't have to. Always returns a
    NumPy array.
    """
    import numpy as np  # type: ignore

    arr = np.asarray(embeddings, dtype=np.float64)
    if arr.size == 0:
        return np.zeros((0, 0), dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(
            f"expected 2-D embeddings, got shape {arr.shape}"
        )
    normed = _normalize_rows(arr)
    return normed @ normed.T


def _global_label(index: int) -> str:
    """0 -> ``SPEAKER_GLOBAL_00``, 10 -> ``SPEAKER_GLOBAL_10`` …."""
    return f"SPEAKER_GLOBAL_{index:02d}"


def reconcile_speakers(
    keys: Sequence[LocalSpeakerKey],
    embeddings,
    *,
    durations: Mapping[LocalSpeakerKey, float] | None = None,
    hard_merge_threshold: float = DEFAULT_HARD_MERGE_THRESHOLD,
    ambiguous_threshold: float = DEFAULT_AMBIGUOUS_THRESHOLD,
) -> ReconciliationResult:
    """Cluster cross-chunk speakers via union-find on cosine similarity.

    Args:
        keys: One :class:`LocalSpeakerKey` per row of ``embeddings``.
            Order must match ``embeddings`` row-for-row.
        embeddings: 2-D array-like, ``len(keys)`` rows. Each row is
            the mean voice embedding for that chunk's speaker.
        durations: Optional total-seconds-per-key. Used to rank
            global clusters biggest-first when assigning IDs. Missing
            keys default to 0; passing ``None`` ranks clusters by
            size of the local-key list (a coarser fallback).
        hard_merge_threshold: Pairs with cosine similarity at or above
            this are merged into the same cluster.
        ambiguous_threshold: Pairs in the half-open band
            ``[ambiguous_threshold, hard_merge_threshold)`` are
            flagged in :attr:`ReconciliationResult.ambiguous_pairs`
            but **not** merged. Must satisfy
            ``ambiguous_threshold <= hard_merge_threshold``.

    Returns:
        :class:`ReconciliationResult` with ``label_map`` ready to use
        for rewriting transcripts.

    Raises:
        ValueError: If the embedding row count and ``len(keys)``
            disagree, or if thresholds are out of order.
    """
    if ambiguous_threshold > hard_merge_threshold:
        raise ValueError(
            f"ambiguous_threshold ({ambiguous_threshold}) must be "
            f"<= hard_merge_threshold ({hard_merge_threshold})"
        )

    result = ReconciliationResult()
    if not keys:
        return result

    import numpy as np  # type: ignore

    arr = np.asarray(embeddings, dtype=np.float64)
    if arr.shape[0] != len(keys):
        raise ValueError(
            f"embedding rows ({arr.shape[0]}) must match key count "
            f"({len(keys)})"
        )

    sim = cosine_similarity_matrix(arr)
    n = len(keys)

    # Union-find with path compression + union by rank.
    parent = list(range(n))
    rank = [0] * n

    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx == ry:
            return
        if rank[rx] < rank[ry]:
            parent[rx] = ry
        elif rank[rx] > rank[ry]:
            parent[ry] = rx
        else:
            parent[ry] = rx
            rank[rx] += 1

    ambiguous: list[AmbiguousMerge] = []
    # Walk the upper triangle once. Hard-merge when above threshold,
    # log when ambiguous, ignore otherwise.
    for i in range(n):
        for j in range(i + 1, n):
            s = float(sim[i, j])
            if s >= hard_merge_threshold:
                union(i, j)
            elif s >= ambiguous_threshold:
                ambiguous.append(
                    AmbiguousMerge(a=keys[i], b=keys[j], similarity=s)
                )

    # Group by root → ranked global labels by total duration.
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    durations = durations or {}

    def _group_duration(idxs: list[int]) -> float:
        total = 0.0
        for k in idxs:
            total += float(durations.get(keys[k], 0.0))
        return total

    # Rank deterministically: bigger total duration first, then by
    # member count, then by smallest chunk_index for stable output
    # when durations tie at zero (a common case in tests).
    ranked = sorted(
        groups.values(),
        key=lambda idxs: (
            -_group_duration(idxs),
            -len(idxs),
            min(keys[k].chunk_index for k in idxs),
            min(keys[k].local_speaker for k in idxs),
        ),
    )

    label_map: dict[LocalSpeakerKey, str] = {}
    cluster_members: dict[str, list[LocalSpeakerKey]] = {}
    cluster_durations: dict[str, float] = {}
    for cluster_idx, idxs in enumerate(ranked):
        label = _global_label(cluster_idx)
        members = [keys[k] for k in idxs]
        cluster_members[label] = members
        cluster_durations[label] = _group_duration(idxs)
        for k in idxs:
            label_map[keys[k]] = label

    result.label_map = label_map
    result.cluster_members = cluster_members
    result.cluster_durations = cluster_durations
    result.ambiguous_pairs = ambiguous
    return result


# ---------------------------------------------------------------------------
# Convenience: rewriting a chunk's transcripts
# ---------------------------------------------------------------------------


def rewrite_speakers_with_global(
    chunks: Iterable["object"],
    chunk_index: int,
    label_map: Mapping[LocalSpeakerKey, str],
) -> list["object"]:
    """Return new VoiceChunks with their speaker re-labelled to global.

    Chunks whose local speaker isn't in ``label_map`` are passed
    through unchanged — the orchestrator emits a warning for those
    and treats them as "leave the local label as-is so downstream
    code can still see them" rather than dropping their data.

    The chunk type is duck-typed (``.speaker`` field, ``with_speaker``
    or fall-through) so the function is testable without importing
    :class:`~src.voice_pack.types.VoiceChunk` directly. In production
    the caller passes :class:`VoiceChunk` instances.
    """
    out: list["object"] = []
    for chunk in chunks:
        local_key = LocalSpeakerKey(
            chunk_index=chunk_index, local_speaker=getattr(chunk, "speaker"),
        )
        new_label = label_map.get(local_key)
        if new_label is None:
            out.append(chunk)
            continue
        out.append(_replace_speaker(chunk, new_label))
    return out


def _replace_speaker(chunk: "object", new_speaker: str) -> "object":
    """Return a copy of ``chunk`` with ``.speaker`` set to ``new_speaker``.

    Uses ``dataclasses.replace`` when possible (the production
    :class:`VoiceChunk` is a frozen dataclass) and otherwise tries to
    construct a new instance from the existing fields. Fallback:
    raise — the caller passed something we can't safely mutate.
    """
    try:
        from dataclasses import is_dataclass, replace  # noqa: WPS433

        if is_dataclass(chunk):
            return replace(chunk, speaker=new_speaker)
    except Exception:  # pragma: no cover - dataclasses always importable
        pass
    raise TypeError(
        f"don't know how to clone {type(chunk).__name__} with new speaker"
    )
