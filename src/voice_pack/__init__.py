"""Voice pack pipeline — multi-speaker voice cloning from source audio.

Stage 1 (this package, initial implementation):
    * Automatic speech recognition (ASR) with word-level timestamps.
    * Speaker diarization (who speaks when).
    * Per-speaker bucketing and quality filtering.
    * CLI that ingests an audio file and reports per-speaker minutes.

Later stages (not yet implemented):
    * Forced alignment against a supplied text (epub / txt).
    * Per-segment emotion tagging.
    * LoRA fine-tune harness on top of base multilingual Chatterbox.

The on-disk artefact format (``pack`` submodule) and the GUI Import
integration are wired; the remaining slices are the training loop and
expression-markup runtime consumption.

Heavy dependencies (``faster-whisper``, ``pyannote.audio``, ``torchaudio``)
are intentionally NOT in the shipped installer. Voice pack preparation is a
CLI/power-user workflow that runs out of an isolated virtualenv so the
PyInstaller bundle stays lean and the auto-update path stays small.
See ``scripts/voice_pack_analyze.py`` for the entry point.
"""

from __future__ import annotations

from .pack import (
    PACK_ARCHIVE_SUFFIX,
    VOICE_PACK_FORMAT_VERSION,
    VoicePack,
    VoicePackError,
    VoicePackMeta,
    base_model_requirements,
    default_voice_packs_root,
    export_pack,
    install_pack,
    install_pack_source,
    list_packs,
    load_pack,
    validate_pack_dir,
)
from .types import (
    EMOTION_CLASSES,
    AsrSegment,
    DatasetClip,
    DatasetManifest,
    DiarTurn,
    SpeakerSummary,
    TaggedChunk,
    VoiceChunk,
    classify_quality_tier,
)

__all__ = [
    "EMOTION_CLASSES",
    "AsrSegment",
    "DatasetClip",
    "DatasetManifest",
    "DiarTurn",
    "SpeakerSummary",
    "TaggedChunk",
    "VoiceChunk",
    "PACK_ARCHIVE_SUFFIX",
    "VOICE_PACK_FORMAT_VERSION",
    "VoicePack",
    "VoicePackError",
    "VoicePackMeta",
    "base_model_requirements",
    "classify_quality_tier",
    "default_voice_packs_root",
    "export_pack",
    "install_pack",
    "install_pack_source",
    "list_packs",
    "load_pack",
    "validate_pack_dir",
]
