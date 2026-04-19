"""Character-level voice clustering for the voice pack pipeline.

Diarization answers "which reader voiced this line?". For a two-reader
audiobook the answer is ``SPEAKER_00`` (the man) and ``SPEAKER_01`` (the
woman). But each reader performs many characters — narrator, villain,
hero, old wizard, child — in acoustically distinct ways. If we train one
LoRA per reader we get two adapters that are each a blend of every
character that reader voices. If we subcluster each reader's chunks on
acoustic similarity we can train one LoRA per character instead.

This module owns the clustering logic. It's a pure function over
embedding vectors — the CLI (:mod:`scripts.voice_pack_characters`) is
responsible for turning audio into embeddings and for file I/O. Keeping
the clustering pure means it tests in milliseconds without audio
libraries, and the embedder can be swapped (Chatterbox voice encoder,
pyannote embeddings, hand-crafted MFCCs, a test fake).

The clustering algorithm is hierarchical-agglomerative with cosine
distance and a single distance threshold, implemented in NumPy. It has
no sklearn/scipy dependency so the module stays importable wherever
NumPy works. The pairwise distance matrix is ``O(N²)`` in memory — for
our use case (≤ ~2000 chunks per speaker per hour of source audio) that
is a few megabytes.

After clustering, clusters below a minimum size/duration are considered
"too small to train a character adapter on" and get merged into the
dominant (typically narrator) cluster. The caller then filters
transcripts by ``character`` in :mod:`scripts.voice_pack_export`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from src.voice_pack.types import VoiceChunk


@dataclass(frozen=True)
class ClusterConfig:
    """Tunable thresholds for character clustering.

    Defaults are tuned for novel audiobooks where the narrator dominates
    and character voices are intentionally distinct. Tighten
    ``distance_threshold`` (e.g. 0.15) to split subtly-different
    characters apart; loosen (e.g. 0.35) to merge close variants.

    ``min_character_seconds`` and ``min_character_chunks`` both gate
    cluster survival — a cluster with 5 chunks totalling 8 seconds is
    not enough data to train a usable adapter, so it gets folded back
    into the dominant cluster rather than emitted as a dead character
    slot.

    ``max_characters_per_speaker`` is a user-facing budget cap: keep at
    most N characters per reader, ranked by total duration. Smaller
    clusters fold into the dominant one. ``None`` means "keep every
    cluster that passes the quality floor". Typical values: 3-5 for a
    "just the main voices" voice pack, None for exhaustive coverage.
    """

    distance_threshold: float = 0.25
    min_character_seconds: float = 60.0
    min_character_chunks: int = 8
    max_characters_per_speaker: int | None = None


@dataclass(frozen=True)
class CharacterSummary:
    """Per-character aggregate stats, ordered biggest first."""

    speaker: str
    character: str
    total_seconds: float
    chunk_count: int
    mean_chunk_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "character": self.character,
            "total_seconds": self.total_seconds,
            "total_minutes": round(self.total_seconds / 60.0, 2),
            "chunk_count": self.chunk_count,
            "mean_chunk_seconds": self.mean_chunk_seconds,
        }


@dataclass
class CharacterClusteringResult:
    """Carries the chunks (with ``character`` now populated) plus stats."""

    chunks: list[VoiceChunk] = field(default_factory=list)
    summaries: list[CharacterSummary] = field(default_factory=list)


def _normalize_rows(matrix):
    """Return a copy of ``matrix`` with each row L2-normalised.

    NumPy is lazy-imported by the callers; this helper assumes ``matrix``
    is already a 2-D ``np.ndarray``.
    """
    import numpy as np  # type: ignore

    if matrix.ndim != 2:
        raise ValueError(f"expected 2-D embeddings, got shape {matrix.shape}")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return matrix / norms


def _agglomerative_cosine(embeddings, distance_threshold: float) -> list[int]:
    """Single-linkage-at-threshold clustering with cosine distance.

    Equivalent to cutting a single-linkage dendrogram at
    ``distance_threshold``: two points land in the same cluster iff
    there's a path through pairs whose cosine distance is
    ``<= distance_threshold``. Implemented as union-find over a
    thresholded distance graph, ``O(N²)`` in memory and roughly
    ``O(N² α(N))`` in time — tractable up to a few thousand rows.

    Chosen for the same reason "single-linkage" would be: a reader's
    character voices form a chain in acoustic space as they modulate
    between performances, and transitively connected performances
    should collapse into one character rather than producing many tiny
    sub-clusters.

    Returns a label per row, renumbered ``0..K-1`` in order of first
    appearance.
    """
    import numpy as np  # type: ignore

    n = embeddings.shape[0]
    if n == 0:
        return []
    if n == 1:
        return [0]

    normed = _normalize_rows(embeddings)
    # Cosine distance = 1 - cosine similarity, for unit vectors.
    sim = normed @ normed.T
    dist = 1.0 - sim

    # Union-find with path compression + union by rank.
    parent = list(range(n))
    rank = [0] * n

    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = parent[root]
        # Path compression.
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

    # Build edges for every pair i<j with distance <= threshold. The
    # vectorised np.argwhere pulls the indices without a Python loop
    # over the matrix.
    upper = np.triu(dist <= distance_threshold, k=1)
    pairs = np.argwhere(upper)
    for i, j in pairs:
        union(int(i), int(j))

    # Relabel by order of first appearance so the output is stable
    # regardless of union-find internals.
    label_of_root: dict[int, int] = {}
    labels: list[int] = []
    for i in range(n):
        root = find(i)
        if root not in label_of_root:
            label_of_root[root] = len(label_of_root)
        labels.append(label_of_root[root])
    return labels


def _rename_by_size(
    labels: list[int],
    durations: list[float],
    config: ClusterConfig,
) -> tuple[list[str | None], int | None]:
    """Map integer cluster ids to ``CHAR_A / CHAR_B / ...`` by total
    duration, fold small clusters into the dominant one, and return
    per-index character labels plus the dominant cluster's label.

    Small-cluster folding is what makes the output usable for training:
    if a cluster has fewer than ``min_character_chunks`` chunks or less
    than ``min_character_seconds`` of audio it is merged into the
    largest cluster (almost always narration). We still emit a label
    for every chunk so downstream filtering is clean; the folded chunks
    just get the dominant label.
    """
    if not labels:
        return [], None

    totals: dict[int, float] = defaultdict(float)
    counts: dict[int, int] = defaultdict(int)
    for lbl, dur in zip(labels, durations):
        totals[lbl] += dur
        counts[lbl] += 1

    # Rank clusters by total seconds (bigger first). Ties broken by count
    # for determinism.
    ranked = sorted(
        totals.keys(),
        key=lambda k: (-totals[k], -counts[k], k),
    )

    # The largest cluster is always kept — even if it fails the
    # thresholds, it's the best we have.
    survivors: set[int] = {ranked[0]}
    for cluster_id in ranked[1:]:
        if (
            counts[cluster_id] >= config.min_character_chunks
            and totals[cluster_id] >= config.min_character_seconds
        ):
            survivors.add(cluster_id)

    # Apply user's budget cap (top-N by size) after quality filtering.
    # Rationale: quality floors drop tiny clusters that are too small to
    # train usefully; the budget cap then picks the top N *training-
    # worthy* characters. Clusters dropped by the cap fold into the
    # dominant cluster the same way quality-rejected ones do.
    if config.max_characters_per_speaker is not None:
        cap = max(1, config.max_characters_per_speaker)
        capped: set[int] = set()
        for cluster_id in ranked:
            if cluster_id in survivors:
                capped.add(cluster_id)
                if len(capped) >= cap:
                    break
        survivors = capped

    # Assign names in rank order. Survivors get CHAR_A, CHAR_B, ...;
    # losers get the dominant survivor's name.
    dominant = ranked[0]
    name_by_cluster: dict[int, str] = {}
    letter_index = 0
    for cluster_id in ranked:
        if cluster_id in survivors:
            name_by_cluster[cluster_id] = _char_label(letter_index)
            letter_index += 1
    dominant_name = name_by_cluster[dominant]
    for cluster_id in ranked:
        if cluster_id not in survivors:
            name_by_cluster[cluster_id] = dominant_name

    return [name_by_cluster[lbl] for lbl in labels], dominant


def _char_label(index: int) -> str:
    """0 → ``CHAR_A``, 1 → ``CHAR_B``, ... 26 → ``CHAR_AA``, 27 → ``CHAR_AB``.

    Plain A..Z suffices for up to 26 characters per reader. Novel
    audiobooks rarely go past ~15 distinct voices; the overflow path
    is here just so the code never runs out of names.
    """
    letters: list[str] = []
    n = index
    while True:
        letters.append(chr(ord("A") + (n % 26)))
        n = n // 26 - 1
        if n < 0:
            break
    return "CHAR_" + "".join(reversed(letters))


def cluster_speaker_chunks(
    chunks: list[VoiceChunk],
    embeddings,
    config: ClusterConfig | None = None,
) -> tuple[list[VoiceChunk], list[CharacterSummary]]:
    """Cluster one speaker's chunks into characters.

    Args:
        chunks: VoiceChunks for a single speaker, in any order. The
            speaker field is used to label the returned summaries; it is
            not used to filter.
        embeddings: 2-D array-like of shape ``(len(chunks), D)``. Row
            ``i`` is the voice embedding for ``chunks[i]``. NumPy array
            is expected but any object with ``.shape`` and matrix-
            multiply support works.
        config: Thresholds. Defaults to :class:`ClusterConfig` defaults.

    Returns:
        ``(new_chunks, summaries)``. ``new_chunks`` are copies of the
        input with ``character`` populated. ``summaries`` are ordered
        biggest first.

    Raises:
        ValueError: If ``len(chunks)`` and row count of ``embeddings``
            disagree, or if chunks span more than one speaker.
    """
    config = config or ClusterConfig()
    if not chunks:
        return [], []

    speakers = {c.speaker for c in chunks}
    if len(speakers) > 1:
        raise ValueError(
            "cluster_speaker_chunks expects chunks from a single speaker; "
            f"got: {sorted(speakers)}"
        )

    import numpy as np  # type: ignore

    emb = np.asarray(embeddings, dtype=np.float64)
    if emb.shape[0] != len(chunks):
        raise ValueError(
            f"embedding rows ({emb.shape[0]}) must match chunk count "
            f"({len(chunks)})"
        )

    raw_labels = _agglomerative_cosine(emb, config.distance_threshold)
    durations = [c.duration for c in chunks]
    names, _dominant = _rename_by_size(raw_labels, durations, config)

    new_chunks = [c.with_character(name) for c, name in zip(chunks, names)]

    # Build summaries.
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for c, name in zip(chunks, names):
        assert name is not None  # _rename_by_size fills every slot
        totals[name] += c.duration
        counts[name] += 1
    speaker_name = next(iter(speakers))
    summaries = [
        CharacterSummary(
            speaker=speaker_name,
            character=name,
            total_seconds=totals[name],
            chunk_count=counts[name],
            mean_chunk_seconds=totals[name] / counts[name] if counts[name] else 0.0,
        )
        for name in sorted(totals.keys(), key=lambda n: (-totals[n], n))
    ]
    return new_chunks, summaries


def cluster_all_speakers(
    chunks: list[VoiceChunk],
    embeddings_by_index: dict[int, Any],
    config: ClusterConfig | None = None,
) -> CharacterClusteringResult:
    """Cluster every speaker independently and return a merged result.

    ``embeddings_by_index`` is keyed by position in ``chunks`` — the
    caller computes embeddings for whatever subset it has audio for and
    passes them in. Chunks without a matching embedding keep their
    existing ``character`` (typically ``None``). That's deliberate: it
    lets the CLI run on a partial audio file (for testing) without
    dropping transcripts.

    Single-chunk speakers, or speakers with only one embedded chunk,
    get labelled ``CHAR_A`` automatically — clustering is trivial but
    we still emit a character tag so downstream tools don't have to
    special-case it.
    """
    config = config or ClusterConfig()

    by_speaker: dict[str, list[tuple[int, VoiceChunk]]] = defaultdict(list)
    for idx, chunk in enumerate(chunks):
        by_speaker[chunk.speaker].append((idx, chunk))

    import numpy as np  # type: ignore

    updated_by_index: dict[int, VoiceChunk] = {}
    all_summaries: list[CharacterSummary] = []

    for speaker, rows in by_speaker.items():
        # Only chunks with a computed embedding are clusterable.
        embeddable = [(i, c) for i, c in rows if i in embeddings_by_index]
        if not embeddable:
            continue

        emb_matrix = np.stack(
            [np.asarray(embeddings_by_index[i], dtype=np.float64) for i, _ in embeddable]
        )
        sub_chunks = [c for _, c in embeddable]
        new_sub, summaries = cluster_speaker_chunks(
            sub_chunks, emb_matrix, config=config
        )
        for (orig_idx, _), new_chunk in zip(embeddable, new_sub):
            updated_by_index[orig_idx] = new_chunk
        all_summaries.extend(summaries)

    merged: list[VoiceChunk] = []
    for idx, chunk in enumerate(chunks):
        merged.append(updated_by_index.get(idx, chunk))

    all_summaries.sort(key=lambda s: (-s.total_seconds, s.speaker, s.character))
    return CharacterClusteringResult(chunks=merged, summaries=all_summaries)
