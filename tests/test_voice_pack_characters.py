"""Unit tests for the character-clustering logic in src/voice_pack/characters.py.

These tests need NumPy (the clustering operates on arrays of embeddings)
but deliberately never touch audio, torch, or the filesystem. Synthetic
embeddings are hand-placed in vector space so the expected cluster
assignments are obvious from the setup.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.voice_pack.characters import (
    ClusterConfig,
    _agglomerative_cosine,
    _char_label,
    _rename_by_size,
    cluster_all_speakers,
    cluster_speaker_chunks,
)
from src.voice_pack.types import VoiceChunk


# ---------------------------------------------------------------------------
# VoiceChunk.character roundtrip
# ---------------------------------------------------------------------------


def test_voice_chunk_character_field_defaults_to_none() -> None:
    chunk = VoiceChunk(
        start=0.0, end=1.0, text="hi", speaker="SPEAKER_00", confidence=1.0
    )
    assert chunk.character is None
    # Round-trip via to_dict/from raw kwargs.
    assert chunk.to_dict()["character"] is None


def test_voice_chunk_with_character_returns_copy() -> None:
    chunk = VoiceChunk(
        start=0.0, end=1.0, text="hi", speaker="SPEAKER_00", confidence=1.0
    )
    updated = chunk.with_character("CHAR_A")
    assert updated is not chunk
    assert updated.character == "CHAR_A"
    # Original untouched (frozen).
    assert chunk.character is None


# ---------------------------------------------------------------------------
# _char_label
# ---------------------------------------------------------------------------


def test_char_label_first_26() -> None:
    assert _char_label(0) == "CHAR_A"
    assert _char_label(1) == "CHAR_B"
    assert _char_label(25) == "CHAR_Z"


def test_char_label_overflow() -> None:
    # Guarantees the code never runs out of names when a reader has more
    # than 26 distinct character voices (unlikely, but it shouldn't
    # crash).
    assert _char_label(26) == "CHAR_AA"
    assert _char_label(27) == "CHAR_AB"


# ---------------------------------------------------------------------------
# _agglomerative_cosine
# ---------------------------------------------------------------------------


def test_agglomerative_three_distinct_clusters() -> None:
    # Three orthogonal directions → three clusters at any reasonable
    # threshold.
    emb = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.99, 0.01, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.98, 0.02],
            [0.0, 0.0, 1.0],
        ]
    )
    labels = _agglomerative_cosine(emb, distance_threshold=0.1)
    # Points 0,1 together, 2,3 together, 4 alone.
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]
    assert labels[4] != labels[0]
    assert labels[4] != labels[2]
    assert len(set(labels)) == 3


def test_agglomerative_loose_threshold_merges_all() -> None:
    emb = np.array(
        [
            [1.0, 0.0],
            [0.0, 1.0],
            [-1.0, 0.0],
        ]
    )
    # Distance threshold above max pairwise distance → one cluster.
    labels = _agglomerative_cosine(emb, distance_threshold=3.0)
    assert len(set(labels)) == 1


def test_agglomerative_tight_threshold_keeps_everyone_separate() -> None:
    emb = np.array(
        [
            [1.0, 0.0],
            [0.8, 0.6],
            [0.0, 1.0],
        ]
    )
    labels = _agglomerative_cosine(emb, distance_threshold=0.0001)
    assert len(set(labels)) == 3


def test_agglomerative_empty() -> None:
    assert _agglomerative_cosine(np.zeros((0, 4)), distance_threshold=0.2) == []


def test_agglomerative_single_row() -> None:
    assert _agglomerative_cosine(np.array([[1.0, 0.0]]), distance_threshold=0.2) == [0]


# ---------------------------------------------------------------------------
# _rename_by_size — small-cluster folding
# ---------------------------------------------------------------------------


def test_rename_folds_small_clusters_into_dominant() -> None:
    # Cluster 0: 10 chunks × 5 s = 50 s; cluster 1: 2 chunks × 5 s = 10 s.
    # With min 5 chunks and 20 s, cluster 1 folds into 0.
    labels = [0] * 10 + [1] * 2
    durations = [5.0] * 12
    config = ClusterConfig(
        min_character_chunks=5, min_character_seconds=20.0
    )
    names, _ = _rename_by_size(labels, durations, config)
    # Every chunk ends up as CHAR_A (cluster 0 is largest).
    assert all(n == "CHAR_A" for n in names)


def test_rename_keeps_survivors_ordered_by_size() -> None:
    # Cluster 0: 5 chunks × 2 s = 10 s (fails size gate)
    # Cluster 1: 20 chunks × 3 s = 60 s (dominant)
    # Cluster 2: 15 chunks × 4 s = 60 s (ties on seconds, loses tie by chunk count)
    labels = [0] * 5 + [1] * 20 + [2] * 15
    durations = [2.0] * 5 + [3.0] * 20 + [4.0] * 15
    config = ClusterConfig(min_character_chunks=10, min_character_seconds=30.0)
    names, _ = _rename_by_size(labels, durations, config)
    # Cluster 1 is the largest → CHAR_A, cluster 2 is second → CHAR_B,
    # cluster 0 is folded into CHAR_A (the dominant).
    assert names[0] == "CHAR_A"  # folded from cluster 0
    assert names[10] == "CHAR_A"  # cluster 1 member
    assert names[25] == "CHAR_B"  # cluster 2 member (first index after cluster 1)


def test_rename_empty_returns_empty() -> None:
    names, dominant = _rename_by_size([], [], ClusterConfig())
    assert names == []
    assert dominant is None


def test_rename_max_characters_caps_survivors() -> None:
    # Three sizeable clusters, all passing quality gates. Cap to top 2:
    # cluster 2 (biggest) → CHAR_A, cluster 1 (middle) → CHAR_B,
    # cluster 0 (smallest) folds into CHAR_A.
    labels = [0] * 10 + [1] * 20 + [2] * 30
    durations = [3.0] * 60
    config = ClusterConfig(
        min_character_chunks=5,
        min_character_seconds=10.0,
        max_characters_per_speaker=2,
    )
    names, _ = _rename_by_size(labels, durations, config)
    distinct = set(names)
    assert distinct == {"CHAR_A", "CHAR_B"}
    # Chunks from cluster 0 were folded into the dominant (cluster 2 → CHAR_A).
    assert names[0] == "CHAR_A"
    # Chunks from cluster 1 kept as CHAR_B.
    assert names[10] == "CHAR_B"
    # Chunks from cluster 2 kept as CHAR_A (dominant).
    assert names[30] == "CHAR_A"


def test_rename_max_characters_one_means_narrator_only() -> None:
    labels = [0] * 10 + [1] * 5
    durations = [2.0] * 15
    config = ClusterConfig(
        min_character_chunks=3,
        min_character_seconds=5.0,
        max_characters_per_speaker=1,
    )
    names, _ = _rename_by_size(labels, durations, config)
    assert set(names) == {"CHAR_A"}


def test_rename_dominant_cluster_survives_even_if_below_thresholds() -> None:
    # Only one cluster, tiny. Must still get a label, since it's the
    # best we have.
    labels = [0, 0]
    durations = [1.0, 1.0]
    config = ClusterConfig(min_character_chunks=100, min_character_seconds=1000.0)
    names, _ = _rename_by_size(labels, durations, config)
    assert names == ["CHAR_A", "CHAR_A"]


# ---------------------------------------------------------------------------
# cluster_speaker_chunks
# ---------------------------------------------------------------------------


def _make_chunk(
    start: float, speaker: str = "SPEAKER_00", dur: float = 3.0
) -> VoiceChunk:
    return VoiceChunk(
        start=start,
        end=start + dur,
        text=f"line at {start}",
        speaker=speaker,
        confidence=1.0,
    )


def test_cluster_speaker_chunks_splits_two_characters() -> None:
    # 10 chunks in direction A (narrator), 10 chunks in direction B
    # (villain). Clustering should produce two characters.
    chunks = [_make_chunk(float(i)) for i in range(20)]
    emb_a = np.tile(np.array([1.0, 0.0, 0.0]), (10, 1))
    emb_b = np.tile(np.array([0.0, 1.0, 0.0]), (10, 1))
    emb = np.vstack([emb_a, emb_b])

    # Need a config whose gates allow both clusters to survive.
    config = ClusterConfig(
        distance_threshold=0.2,
        min_character_seconds=10.0,
        min_character_chunks=5,
    )
    new_chunks, summaries = cluster_speaker_chunks(chunks, emb, config=config)
    char_set = {c.character for c in new_chunks}
    assert char_set == {"CHAR_A", "CHAR_B"}
    assert len(summaries) == 2
    # Summaries are sorted by total seconds descending; both clusters
    # have equal totals here (10 × 3 s each) so the order falls back to
    # character name.
    assert {s.character for s in summaries} == {"CHAR_A", "CHAR_B"}


def test_cluster_speaker_chunks_empty() -> None:
    new_chunks, summaries = cluster_speaker_chunks([], np.zeros((0, 4)))
    assert new_chunks == []
    assert summaries == []


def test_cluster_speaker_chunks_mismatched_shapes_raises() -> None:
    chunks = [_make_chunk(0.0)]
    emb = np.zeros((2, 4))  # wrong row count
    with pytest.raises(ValueError, match="must match chunk count"):
        cluster_speaker_chunks(chunks, emb)


def test_cluster_speaker_chunks_multi_speaker_raises() -> None:
    chunks = [
        _make_chunk(0.0, speaker="SPEAKER_00"),
        _make_chunk(3.0, speaker="SPEAKER_01"),
    ]
    emb = np.eye(2, 4)
    with pytest.raises(ValueError, match="single speaker"):
        cluster_speaker_chunks(chunks, emb)


# ---------------------------------------------------------------------------
# cluster_all_speakers
# ---------------------------------------------------------------------------


def test_cluster_all_speakers_handles_multiple_speakers() -> None:
    # Two speakers, each with two character clusters. Characters
    # re-use CHAR_A/CHAR_B letters — they're only unique within a
    # speaker, not globally.
    chunks: list[VoiceChunk] = []
    for i in range(10):
        chunks.append(_make_chunk(float(i), speaker="SPEAKER_00"))
    for i in range(10):
        chunks.append(_make_chunk(float(i + 100), speaker="SPEAKER_01"))

    # SPEAKER_00: first 5 in direction A, next 5 in direction B.
    # SPEAKER_01: first 5 in direction C, next 5 in direction D.
    embeddings_by_index: dict[int, np.ndarray] = {}
    for i in range(5):
        embeddings_by_index[i] = np.array([1.0, 0.0, 0.0, 0.0])
    for i in range(5, 10):
        embeddings_by_index[i] = np.array([0.0, 1.0, 0.0, 0.0])
    for i in range(10, 15):
        embeddings_by_index[i] = np.array([0.0, 0.0, 1.0, 0.0])
    for i in range(15, 20):
        embeddings_by_index[i] = np.array([0.0, 0.0, 0.0, 1.0])

    config = ClusterConfig(
        distance_threshold=0.2,
        min_character_seconds=5.0,
        min_character_chunks=3,
    )
    result = cluster_all_speakers(chunks, embeddings_by_index, config=config)
    assert len(result.chunks) == 20
    by_speaker: dict[str, set[str]] = {"SPEAKER_00": set(), "SPEAKER_01": set()}
    for c in result.chunks:
        assert c.character is not None
        by_speaker[c.speaker].add(c.character)
    # Each speaker has two characters, labelled CHAR_A and CHAR_B.
    assert by_speaker["SPEAKER_00"] == {"CHAR_A", "CHAR_B"}
    assert by_speaker["SPEAKER_01"] == {"CHAR_A", "CHAR_B"}
    # Summaries aggregate per (speaker, character).
    assert len(result.summaries) == 4


def test_cluster_all_speakers_preserves_unembedded_chunks() -> None:
    # A chunk without a supplied embedding keeps its original character
    # (typically None).
    chunks = [
        _make_chunk(0.0, speaker="SPEAKER_00"),
        _make_chunk(3.0, speaker="SPEAKER_00"),
        _make_chunk(6.0, speaker="SPEAKER_00"),
    ]
    embeddings_by_index = {
        0: np.array([1.0, 0.0]),
        1: np.array([1.0, 0.0]),
        # Index 2 deliberately not embedded.
    }
    result = cluster_all_speakers(chunks, embeddings_by_index)
    assert result.chunks[0].character is not None
    assert result.chunks[1].character is not None
    assert result.chunks[2].character is None
