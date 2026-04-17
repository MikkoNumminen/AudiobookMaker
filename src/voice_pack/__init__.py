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
    * Voice pack artifact format (weights + metadata + sample).
    * GUI "Import voice pack" integration.

Heavy dependencies (``faster-whisper``, ``pyannote.audio``, ``torchaudio``)
are intentionally NOT in the shipped installer. Voice pack preparation is a
CLI/power-user workflow that runs out of an isolated virtualenv so the
PyInstaller bundle stays lean and the auto-update path stays small.
See ``scripts/voice_pack_analyze.py`` for the entry point.
"""

from __future__ import annotations

from .types import (
    AsrSegment,
    DiarTurn,
    SpeakerSummary,
    VoiceChunk,
    classify_quality_tier,
)

__all__ = [
    "AsrSegment",
    "DiarTurn",
    "SpeakerSummary",
    "VoiceChunk",
    "classify_quality_tier",
]
