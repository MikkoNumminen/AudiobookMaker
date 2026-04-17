"""Shared data types for the voice pack pipeline.

These are the contract between the ASR, diarization, bucketing, alignment,
emotion tagging, dataset export, training, and loader stages. Every record
is a plain dataclass so it serialises cleanly to JSON and YAML without
custom encoders.

Quality tiers (see :func:`classify_quality_tier`):

* ``"full_lora"`` — ≥ 30 min of clean audio. Primary voice, full LoRA
  fine-tune is worth the GPU cost.
* ``"reduced_lora"`` — 10–30 min. LoRA fine-tune at reduced rank with
  early stopping; flagged as "experimental quality" in the UI.
* ``"few_shot"`` — 1–10 min. Not enough to fine-tune. Instead, extract
  the best ~15 s reference clips and save them as a classic few-shot
  preset (the existing ref-clip path).
* ``"skip"`` — < 1 min. Not enough data for anything useful. Ignored.

Emotion labels (SpeechBrain IEMOCAP classifier output):

* ``"neutral"``, ``"angry"``, ``"happy"``, ``"sad"`` — the four
  canonical classes.
* ``"unknown"`` — classifier returned nothing, or emotion tagging was
  skipped for this chunk.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

EMOTION_CLASSES: tuple[str, ...] = ("neutral", "angry", "happy", "sad", "unknown")


@dataclass(frozen=True)
class AsrSegment:
    """One transcribed segment from the ASR stage.

    ``confidence`` is the mean token-level log-probability normalised to
    ``[0.0, 1.0]`` where available, or ``1.0`` when the ASR backend does not
    report per-segment confidence. It is used downstream as a quality signal,
    not as a hard gate.
    """

    start: float  # seconds from the start of the source audio
    end: float
    text: str
    confidence: float = 1.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiarTurn:
    """One speaker turn from the diarization stage.

    ``speaker`` is an opaque id produced by the diarizer (e.g.
    ``"SPEAKER_00"``). The id is stable within a single diarization run but
    NOT across runs — two different files will both have a ``SPEAKER_00``
    and those are not the same person.
    """

    start: float
    end: float
    speaker: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VoiceChunk:
    """A merged unit — one sentence attributed to one speaker.

    Produced by :mod:`src.voice_pack.bucket` by intersecting ASR segments
    with diarization turns. This is the atomic training unit for the later
    fine-tune stage.
    """

    start: float
    end: float
    text: str
    speaker: str
    confidence: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaggedChunk:
    """A :class:`VoiceChunk` with emotion metadata attached.

    Produced by :mod:`src.voice_pack.emotion`. The emotion label is used
    downstream by :mod:`src.voice_pack.dataset` to rebalance the training
    set so minority classes (angry, sad) imprint despite being rare in
    typical narration.

    ``emotion_confidence`` is the softmax probability of the predicted
    class, in ``[0.0, 1.0]``. Classifiers that don't expose per-class
    probabilities should return ``1.0`` for their top class.
    """

    start: float
    end: float
    text: str
    speaker: str
    confidence: float
    emotion: str
    emotion_confidence: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_chunk(
        cls,
        chunk: VoiceChunk,
        *,
        emotion: str = "unknown",
        emotion_confidence: float = 0.0,
    ) -> "TaggedChunk":
        """Promote a :class:`VoiceChunk` to a :class:`TaggedChunk`."""
        return cls(
            start=chunk.start,
            end=chunk.end,
            text=chunk.text,
            speaker=chunk.speaker,
            confidence=chunk.confidence,
            emotion=emotion,
            emotion_confidence=emotion_confidence,
        )


@dataclass(frozen=True)
class DatasetClip:
    """One exported training clip on disk.

    ``path`` is relative to the manifest's ``root_dir`` so the dataset
    folder is portable — copy the whole folder to a training host and
    nothing breaks.
    """

    path: str
    text: str
    emotion: str
    speaker: str
    duration: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DatasetManifest:
    """Manifest describing a per-speaker training dataset.

    Consumed by the Slice-3 LoRA training harness. The manifest lives on
    disk as ``manifest.json`` inside ``root_dir``; ``clips`` references
    audio files in the same directory via their relative ``path``.
    """

    speaker: str
    root_dir: Path
    clips: list[DatasetClip] = field(default_factory=list)
    total_seconds: float = 0.0
    emotion_counts: dict[str, int] = field(default_factory=dict)
    sample_rate_hz: int = 24000

    def to_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "root_dir": str(self.root_dir),
            "sample_rate_hz": self.sample_rate_hz,
            "total_seconds": self.total_seconds,
            "emotion_counts": dict(self.emotion_counts),
            "clips": [c.to_dict() for c in self.clips],
        }


@dataclass
class SpeakerSummary:
    """Aggregate statistics for one detected speaker.

    ``quality_tier`` is derived from ``total_seconds`` via
    :func:`classify_quality_tier`. It drives the branch in Stage 2: whether
    to fine-tune, extract a few-shot ref clip, or skip entirely.
    """

    speaker: str
    total_seconds: float
    chunk_count: int
    mean_chunk_seconds: float
    quality_tier: str
    chunks: list[VoiceChunk] = field(default_factory=list, repr=False, compare=False)

    def to_dict(self, *, include_chunks: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "speaker": self.speaker,
            "total_seconds": self.total_seconds,
            "total_minutes": round(self.total_seconds / 60.0, 2),
            "chunk_count": self.chunk_count,
            "mean_chunk_seconds": self.mean_chunk_seconds,
            "quality_tier": self.quality_tier,
        }
        if include_chunks:
            data["chunks"] = [c.to_dict() for c in self.chunks]
        return data


# Quality tier thresholds, in seconds. Centralised here so the CLI, the
# fine-tune harness, and the GUI all agree on the same cutoffs.
TIER_FULL_LORA_MIN_S = 30 * 60  # 30 minutes
TIER_REDUCED_LORA_MIN_S = 10 * 60  # 10 minutes
TIER_FEW_SHOT_MIN_S = 1 * 60  # 1 minute


def classify_quality_tier(total_seconds: float) -> str:
    """Return the training tier for a speaker based on total clean audio.

    >>> classify_quality_tier(40 * 60)
    'full_lora'
    >>> classify_quality_tier(15 * 60)
    'reduced_lora'
    >>> classify_quality_tier(3 * 60)
    'few_shot'
    >>> classify_quality_tier(10)
    'skip'
    """
    if total_seconds >= TIER_FULL_LORA_MIN_S:
        return "full_lora"
    if total_seconds >= TIER_REDUCED_LORA_MIN_S:
        return "reduced_lora"
    if total_seconds >= TIER_FEW_SHOT_MIN_S:
        return "few_shot"
    return "skip"
