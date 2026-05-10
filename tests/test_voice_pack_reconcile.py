"""Unit tests for :mod:`src.voice_pack.reconcile`.

Synthetic embeddings only — no real audio, no models. NumPy is the
only heavy dependency and the tests exercise the union-find / cosine
math directly.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.voice_pack.reconcile import (
    DEFAULT_AMBIGUOUS_THRESHOLD,
    DEFAULT_HARD_MERGE_THRESHOLD,
    AmbiguousMerge,
    LocalSpeakerKey,
    ReconciliationResult,
    cosine_similarity_matrix,
    reconcile_speakers,
    rewrite_speakers_with_global,
)
from src.voice_pack.types import VoiceChunk


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _unit(vec: list[float]) -> np.ndarray:
    a = np.asarray(vec, dtype=np.float64)
    return a / np.linalg.norm(a)


def _make_keys(*pairs: tuple[int, str]) -> list[LocalSpeakerKey]:
    return [LocalSpeakerKey(chunk_index=i, local_speaker=s) for i, s in pairs]


# ---------------------------------------------------------------------------
# cosine_similarity_matrix
# ---------------------------------------------------------------------------


class TestCosineSimilarityMatrix:
    def test_empty_input(self) -> None:
        out = cosine_similarity_matrix(np.zeros((0, 4)))
        assert out.shape == (0, 0)

    def test_unit_vectors_identity(self) -> None:
        eye = np.eye(3)
        out = cosine_similarity_matrix(eye)
        # Diagonal exactly 1.0; off-diagonal exactly 0.0 for an
        # orthonormal basis.
        assert np.allclose(np.diag(out), 1.0)
        off_diag = out - np.diag(np.diag(out))
        assert np.allclose(off_diag, 0.0)

    def test_identical_rows_score_one(self) -> None:
        v = _unit([1.0, 2.0, 3.0])
        m = np.stack([v, v, v])
        out = cosine_similarity_matrix(m)
        assert np.allclose(out, 1.0)

    def test_zero_vector_does_not_blow_up(self) -> None:
        # Defensive: all-zero rows would normally divide by zero. The
        # helper guards against it and produces zero similarities.
        m = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
        out = cosine_similarity_matrix(m)
        # No NaN.
        assert not np.isnan(out).any()


# ---------------------------------------------------------------------------
# reconcile_speakers — happy path
# ---------------------------------------------------------------------------


class TestReconcileHappyPath:
    def test_empty_input(self) -> None:
        result = reconcile_speakers([], np.zeros((0, 4)))
        assert result.label_map == {}
        assert result.cluster_members == {}
        assert result.ambiguous_pairs == []

    def test_single_speaker_one_chunk(self) -> None:
        keys = _make_keys((0, "SPEAKER_00"))
        result = reconcile_speakers(keys, np.array([[1.0, 0.0]]))
        assert result.label_map == {keys[0]: "SPEAKER_GLOBAL_00"}
        assert result.ambiguous_pairs == []

    def test_two_chunks_same_speaker_merge(self) -> None:
        # Two chunks, each emitted SPEAKER_00. Their embeddings are
        # nearly identical → must merge to one global label.
        keys = _make_keys((0, "SPEAKER_00"), (1, "SPEAKER_00"))
        emb = np.stack([_unit([1.0, 0.1, 0.0]), _unit([1.0, 0.11, 0.0])])
        result = reconcile_speakers(keys, emb)
        assert result.label_map[keys[0]] == result.label_map[keys[1]]
        # Only one global cluster.
        assert len(set(result.label_map.values())) == 1

    def test_two_chunks_different_speakers_split(self) -> None:
        # Two chunks, distinct speakers — orthogonal embeddings stay
        # in separate global clusters.
        keys = _make_keys((0, "SPEAKER_00"), (1, "SPEAKER_00"))
        emb = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        result = reconcile_speakers(keys, emb)
        assert result.label_map[keys[0]] != result.label_map[keys[1]]
        assert len(result.cluster_members) == 2

    def test_three_speakers_two_chunks(self) -> None:
        # Chunk 0 has Alice + Bob; chunk 1 has Alice + Carol. We
        # expect three global clusters (A, B, C) with Alice merging
        # across chunks.
        keys = _make_keys(
            (0, "SPEAKER_00"),  # Alice in chunk 0
            (0, "SPEAKER_01"),  # Bob in chunk 0
            (1, "SPEAKER_00"),  # Alice in chunk 1 (different label!)
            (1, "SPEAKER_01"),  # Carol in chunk 1
        )
        # Distinct unit vectors per identity; Alice rows nearly equal.
        alice = _unit([1.0, 0.0, 0.0, 0.0])
        bob = _unit([0.0, 1.0, 0.0, 0.0])
        carol = _unit([0.0, 0.0, 1.0, 0.0])
        emb = np.stack([alice, bob, alice + 0.01, carol])
        result = reconcile_speakers(keys, emb)
        assert result.label_map[keys[0]] == result.label_map[keys[2]]
        # Three distinct global clusters total.
        assert len(set(result.label_map.values())) == 3


class TestReconcileRanking:
    def test_largest_cluster_gets_speaker_global_00(self) -> None:
        # Three keys, two of which are the same person and dominate
        # by total duration. The big cluster should land at index 00.
        keys = _make_keys(
            (0, "SPEAKER_00"),  # solo speaker
            (1, "SPEAKER_00"),  # narrator pair (chunk 1)
            (2, "SPEAKER_00"),  # narrator pair (chunk 2)
        )
        solo = _unit([0.0, 1.0, 0.0])
        nar = _unit([1.0, 0.0, 0.0])
        emb = np.stack([solo, nar, nar + 0.01])
        durations = {keys[0]: 10.0, keys[1]: 200.0, keys[2]: 200.0}
        result = reconcile_speakers(keys, emb, durations=durations)
        # Narrator (keys 1 & 2) dominates by duration → global 00.
        assert result.label_map[keys[1]] == "SPEAKER_GLOBAL_00"
        assert result.label_map[keys[2]] == "SPEAKER_GLOBAL_00"
        assert result.label_map[keys[0]] == "SPEAKER_GLOBAL_01"

    def test_ranking_falls_back_to_member_count_without_durations(self) -> None:
        keys = _make_keys(
            (0, "SPEAKER_00"),  # solo
            (1, "SPEAKER_00"),  # cluster A pair
            (2, "SPEAKER_00"),  # cluster A pair
        )
        a = _unit([1.0, 0.0])
        b = _unit([0.0, 1.0])
        emb = np.stack([b, a, a + 0.01])
        result = reconcile_speakers(keys, emb)
        # Pair-cluster (size 2) ranks above solo (size 1).
        big = result.label_map[keys[1]]
        assert big == "SPEAKER_GLOBAL_00"
        assert result.label_map[keys[0]] == "SPEAKER_GLOBAL_01"


class TestReconcileAmbiguous:
    def test_ambiguous_band_logged_not_merged(self) -> None:
        # Two embeddings with cosine similarity in the grey band.
        # The reconciler must NOT merge them but must log the pair.
        keys = _make_keys((0, "SPEAKER_00"), (1, "SPEAKER_00"))
        # Construct vectors with cosine sim ≈ 0.7 (grey band by default).
        a = _unit([1.0, 0.0])
        b = _unit([1.0, 1.0])  # angle 45°, cos ≈ 0.707
        emb = np.stack([a, b])
        result = reconcile_speakers(
            keys, emb,
            hard_merge_threshold=0.9,
            ambiguous_threshold=0.6,
        )
        assert result.label_map[keys[0]] != result.label_map[keys[1]]
        assert len(result.ambiguous_pairs) == 1
        flagged = result.ambiguous_pairs[0]
        assert {flagged.a, flagged.b} == {keys[0], keys[1]}
        assert 0.6 <= flagged.similarity < 0.9

    def test_pair_above_hard_threshold_not_logged(self) -> None:
        # When a pair gets hard-merged, it must not also appear in
        # the ambiguous list.
        keys = _make_keys((0, "SPEAKER_00"), (1, "SPEAKER_00"))
        v = _unit([1.0, 0.1])
        emb = np.stack([v, v])
        result = reconcile_speakers(keys, emb)
        assert result.ambiguous_pairs == []

    def test_pair_below_ambiguous_threshold_silently_ignored(self) -> None:
        keys = _make_keys((0, "SPEAKER_00"), (1, "SPEAKER_00"))
        emb = np.array([[1.0, 0.0], [0.0, 1.0]])  # cos sim = 0
        result = reconcile_speakers(keys, emb)
        assert result.ambiguous_pairs == []


class TestReconcileValidation:
    def test_threshold_order_enforced(self) -> None:
        with pytest.raises(ValueError):
            reconcile_speakers(
                _make_keys((0, "SPEAKER_00")),
                np.array([[1.0, 0.0]]),
                hard_merge_threshold=0.5,
                ambiguous_threshold=0.9,
            )

    def test_row_count_must_match_keys(self) -> None:
        with pytest.raises(ValueError):
            reconcile_speakers(
                _make_keys((0, "SPEAKER_00")),
                np.array([[1.0, 0.0], [0.5, 0.5]]),
            )


# ---------------------------------------------------------------------------
# rewrite_speakers_with_global
# ---------------------------------------------------------------------------


class TestRewriteSpeakers:
    def test_rewrite_replaces_speaker_field(self) -> None:
        chunks = [
            VoiceChunk(start=0.0, end=1.0, text="hi", speaker="SPEAKER_00",
                       confidence=0.9),
            VoiceChunk(start=1.0, end=2.0, text="yo", speaker="SPEAKER_01",
                       confidence=0.8),
        ]
        label_map = {
            LocalSpeakerKey(chunk_index=3, local_speaker="SPEAKER_00"): "SPEAKER_GLOBAL_00",
            LocalSpeakerKey(chunk_index=3, local_speaker="SPEAKER_01"): "SPEAKER_GLOBAL_01",
        }
        rewritten = rewrite_speakers_with_global(chunks, 3, label_map)
        assert rewritten[0].speaker == "SPEAKER_GLOBAL_00"
        assert rewritten[1].speaker == "SPEAKER_GLOBAL_01"
        # Original chunks untouched (frozen dataclass).
        assert chunks[0].speaker == "SPEAKER_00"

    def test_unknown_local_speaker_passes_through(self) -> None:
        # Defensive: if the embedder skipped a local speaker (e.g. the
        # speaker had no audio long enough to embed), we leave the
        # local label alone rather than dropping the chunk.
        chunks = [
            VoiceChunk(start=0.0, end=1.0, text="hi", speaker="SPEAKER_99",
                       confidence=0.9),
        ]
        rewritten = rewrite_speakers_with_global(chunks, 0, {})
        assert rewritten[0].speaker == "SPEAKER_99"

    def test_does_not_mutate_other_fields(self) -> None:
        chunk = VoiceChunk(
            start=1.5, end=4.7, text="hello there",
            speaker="SPEAKER_00", confidence=0.42, character="CHAR_A",
        )
        label_map = {
            LocalSpeakerKey(chunk_index=0, local_speaker="SPEAKER_00"): "SPEAKER_GLOBAL_00",
        }
        rewritten = rewrite_speakers_with_global([chunk], 0, label_map)
        out = rewritten[0]
        assert out.start == 1.5
        assert out.end == 4.7
        assert out.text == "hello there"
        assert out.confidence == 0.42
        assert out.character == "CHAR_A"
        assert out.speaker == "SPEAKER_GLOBAL_00"


# ---------------------------------------------------------------------------
# default thresholds
# ---------------------------------------------------------------------------


def test_defaults_are_in_sane_order() -> None:
    assert DEFAULT_AMBIGUOUS_THRESHOLD < DEFAULT_HARD_MERGE_THRESHOLD
    assert 0.0 < DEFAULT_AMBIGUOUS_THRESHOLD < 1.0
    assert 0.0 < DEFAULT_HARD_MERGE_THRESHOLD <= 1.0
