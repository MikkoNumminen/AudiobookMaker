"""TTS engine module for AudiobookMaker.

Converts text to speech using edge-tts and combines audio chunks with pydub.
Supports Finnish and English voices with configurable speed.

This module used to contain the full pipeline (Finnish normalizer, chunking,
audio combine, edge-tts synthesis). It has since been split into four
focused modules:

- :mod:`src.tts_normalizer_fi` — Finnish text normalizer (``normalize_finnish_text``).
- :mod:`src.tts_chunking` — sentence splitter and chunker (``split_text_into_chunks``).
- :mod:`src.tts_audio` — pydub/ffmpeg combine helpers (``combine_audio_files``).
- :mod:`src.tts_engine` (this file) — Edge-TTS synthesis, ``TTSConfig``,
  ``text_to_speech``, ``chapters_to_speech``, plus re-exports of the public
  symbols above so that existing callers keep working unchanged.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# NOTE: edge_tts is imported lazily inside _synthesize_chunk() so that
# other consumers (e.g. dev_qwen_tts.py) can `from src.tts_engine import
# split_text_into_chunks` without dragging in an online-TTS dependency
# they don't need.

# Re-exports — keep the public surface of the old monolith intact so
# existing call sites like `from src.tts_engine import normalize_finnish_text`
# keep working.
from src.tts_normalizer_fi import (  # noqa: F401
    normalize_finnish_text,
    _expand_abbreviations,
    _expand_acronyms,
    _expand_roman_numerals,
    _expand_unit_symbols,
    _fi_detect_case,
    _fi_split_number_compounds,
    _roman_to_int,
)
from src.tts_chunking import (  # noqa: F401
    MAX_CHUNK_CHARS,
    split_text_into_chunks,
    _force_split,
    _split_sentences,
    _ABBREVIATIONS,
    _SENTENCE_END,
)
from src.tts_audio import (  # noqa: F401
    combine_audio_files,
    _load_audio_with_retry,
    _trim_chunk_silence,
)
from src.tts_normalizer import normalize_text  # noqa: F401


# ---------------------------------------------------------------------------
# Voice configuration
# ---------------------------------------------------------------------------

# voice_id -> display name shown in the GUI
VOICE_DISPLAY_NAMES: dict[str, str] = {
    # Finnish
    "fi-FI-NooraNeural": "Noora (suomi, nainen)",
    "fi-FI-HarriNeural": "Harri (suomi, mies)",
    "fi-FI-SelmaNeural": "Selma (suomi, nainen)",
    # English US
    "en-US-JennyNeural": "Jenny (English US, female)",
    "en-US-AriaNeural": "Aria (English US, female)",
    "en-US-AvaNeural": "Ava (English US, female)",
    "en-US-GuyNeural": "Guy (English US, male)",
    "en-US-AndrewNeural": "Andrew (English US, male)",
    "en-US-BrianNeural": "Brian (English US, male)",
    "en-US-EmmaNeural": "Emma (English US, female)",
    "en-US-MichelleNeural": "Michelle (English US, female)",
    # English GB
    "en-GB-SoniaNeural": "Sonia (English GB, female)",
    "en-GB-RyanNeural": "Ryan (English GB, male)",
    "en-GB-LibbyNeural": "Libby (English GB, female)",
    "en-GB-ThomasNeural": "Thomas (English GB, male)",
    # German
    "de-DE-KatjaNeural": "Katja (Deutsch, weiblich)",
    "de-DE-ConradNeural": "Conrad (Deutsch, männlich)",
    "de-DE-AmalaNeural": "Amala (Deutsch, weiblich)",
    # Swedish
    "sv-SE-SofieNeural": "Sofie (svenska, kvinna)",
    "sv-SE-MattiasNeural": "Mattias (svenska, man)",
    # French
    "fr-FR-DeniseNeural": "Denise (français, femme)",
    "fr-FR-HenriNeural": "Henri (français, homme)",
    # Spanish
    "es-ES-ElviraNeural": "Elvira (español, mujer)",
    "es-ES-AlvaroNeural": "Alvaro (español, hombre)",
}

VOICES: dict[str, dict[str, str]] = {
    "fi": {
        "default": "fi-FI-NooraNeural",
        "Noora (suomi, nainen)": "fi-FI-NooraNeural",
        "Harri (suomi, mies)": "fi-FI-HarriNeural",
        "Selma (suomi, nainen)": "fi-FI-SelmaNeural",
    },
    "en": {
        "default": "en-US-JennyNeural",
        "Jenny (English US, female)": "en-US-JennyNeural",
        "Aria (English US, female)": "en-US-AriaNeural",
        "Ava (English US, female)": "en-US-AvaNeural",
        "Guy (English US, male)": "en-US-GuyNeural",
        "Andrew (English US, male)": "en-US-AndrewNeural",
        "Brian (English US, male)": "en-US-BrianNeural",
        "Emma (English US, female)": "en-US-EmmaNeural",
        "Michelle (English US, female)": "en-US-MichelleNeural",
        "Sonia (English GB, female)": "en-GB-SoniaNeural",
        "Ryan (English GB, male)": "en-GB-RyanNeural",
        "Libby (English GB, female)": "en-GB-LibbyNeural",
        "Thomas (English GB, male)": "en-GB-ThomasNeural",
    },
    "de": {
        "default": "de-DE-KatjaNeural",
        "Katja (Deutsch, weiblich)": "de-DE-KatjaNeural",
        "Conrad (Deutsch, männlich)": "de-DE-ConradNeural",
        "Amala (Deutsch, weiblich)": "de-DE-AmalaNeural",
    },
    "sv": {
        "default": "sv-SE-SofieNeural",
        "Sofie (svenska, kvinna)": "sv-SE-SofieNeural",
        "Mattias (svenska, man)": "sv-SE-MattiasNeural",
    },
    "fr": {
        "default": "fr-FR-DeniseNeural",
        "Denise (français, femme)": "fr-FR-DeniseNeural",
        "Henri (français, homme)": "fr-FR-HenriNeural",
    },
    "es": {
        "default": "es-ES-ElviraNeural",
        "Elvira (español, mujer)": "es-ES-ElviraNeural",
        "Alvaro (español, hombre)": "es-ES-AlvaroNeural",
    },
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class TTSConfig:
    """Configuration for a TTS conversion job."""

    language: str = "fi"
    voice: str = ""          # empty = use language default
    rate: str = "+0%"        # e.g. "+10%", "-20%"
    volume: str = "+0%"
    normalize_text: bool = True
    """If True and language == "fi", run the input through
    :func:`normalize_finnish_text` before chunking. This expands years,
    century expressions, numeric ranges, and elided-hyphen compounds into
    word-form Finnish so every engine (Edge-TTS, Piper, Chatterbox, ...)
    pronounces them correctly. Set False to pass raw text through."""

    year_shortening: str = "radio"
    """Controls how 4-digit Finnish year literals are read aloud. The
    default ``"radio"`` follows the Kielikello radio-announcer
    convention where years stay in nominative regardless of the
    governing preposition (`vuodesta 1917` → "vuodesta tuhat
    yhdeksänsataa seitsemäntoista"). Set to ``"full"`` to emit full
    case agreement per VISK §772 (`vuodesta 1917` → "vuodesta
    tuhannesta yhdeksästäsadastaseitsemästätoista"). Only affects
    years in the 1000–2100 range; other integers always follow the
    governor-word table in :mod:`src.tts_normalizer_fi`."""

    def resolved_voice(self) -> str:
        if self.voice:
            return self.voice
        lang = self.language if self.language in VOICES else "fi"
        return VOICES[lang]["default"]


ProgressCallback = Callable[[int, int, str], None]
"""Callback(current_chunk, total_chunks, status_message)."""


# ---------------------------------------------------------------------------
# edge-tts async core
# ---------------------------------------------------------------------------


# Per-chunk timeout in seconds.  Edge-tts normally takes 2-10 seconds per
# chunk; 120 seconds is generous enough to handle slow connections while
# still catching genuine stalls.
_EDGE_CHUNK_TIMEOUT = 120


async def _synthesize_chunk(
    text: str,
    voice: str,
    rate: str,
    volume: str,
    output_path: str,
) -> None:
    """Synthesize a single chunk to an MP3 file via edge-tts."""
    import edge_tts  # lazy import — see note at top of file

    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=volume)
    try:
        await asyncio.wait_for(communicate.save(output_path), timeout=_EDGE_CHUNK_TIMEOUT)
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"Edge-TTS timed out after {_EDGE_CHUNK_TIMEOUT}s synthesizing a chunk. "
            "Check your internet connection."
        )


async def _synthesize_all_chunks(
    chunks: list[str],
    config: TTSConfig,
    tmp_dir: str,
    progress_cb: Optional[ProgressCallback],
) -> list[str]:
    """Synthesize all chunks sequentially and return list of MP3 file paths."""
    voice = config.resolved_voice()
    output_files: list[str] = []
    total = len(chunks)

    for i, chunk in enumerate(chunks):
        out_path = os.path.join(tmp_dir, f"chunk_{i:04d}.mp3")
        if progress_cb:
            progress_cb(i, total, f"Syntetisoidaan pala {i + 1}/{total}…")
        await _synthesize_chunk(chunk, voice, config.rate, config.volume, out_path)
        output_files.append(out_path)

    if progress_cb:
        progress_cb(total, total, "Yhdistetään äänitiedostot…")

    return output_files


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def text_to_speech(
    text: str,
    output_path: str | Path,
    config: Optional[TTSConfig] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> None:
    """Convert text to an MP3 file.

    Splits text into chunks, synthesizes each with edge-tts, then combines.

    Args:
        text: Input text to convert.
        output_path: Destination MP3 file path.
        config: TTS configuration (voice, language, speed). Defaults to Finnish.
        progress_cb: Optional callback(current, total, message) for progress updates.

    Raises:
        ValueError: If text is empty.
        RuntimeError: If synthesis or audio combination fails.
    """
    if not text.strip():
        raise ValueError("Cannot synthesize empty text.")

    if config is None:
        config = TTSConfig()

    output_path = str(output_path)

    if config.normalize_text:
        text = normalize_text(
            text,
            config.language,
            year_shortening=config.year_shortening,
        )

    chunks = split_text_into_chunks(text)

    if not chunks:
        raise ValueError("Text produced no chunks after splitting.")

    tmp_dir = tempfile.mkdtemp(prefix="abm_tts_")
    try:
        mp3_files = asyncio.run(
            _synthesize_all_chunks(chunks, config, tmp_dir, progress_cb)
        )
        # On Windows, edge-tts's async transports may hold file handles
        # briefly after asyncio.run() returns. Force GC to release them.
        import gc
        gc.collect()
        combine_audio_files(mp3_files, output_path)
    finally:
        # Clean up temp files. On Windows, pydub may hold locks briefly
        # so we retry with a small delay if deletion fails.
        import shutil
        import time
        for attempt in range(3):
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                break
            except OSError:
                time.sleep(0.5)

    if progress_cb:
        progress_cb(len(chunks), len(chunks), "Valmis!")


def chapters_to_speech(
    chapters: list[tuple[str, str]],  # (title, content)
    output_dir: str | Path,
    config: Optional[TTSConfig] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> list[str]:
    """Convert multiple chapters to individual MP3 files.

    Args:
        chapters: List of (title, content) tuples.
        output_dir: Directory where MP3 files will be saved.
        config: TTS configuration.
        progress_cb: Optional progress callback.

    Returns:
        List of created MP3 file paths.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_files: list[str] = []
    total_chapters = len(chapters)

    for idx, (title, content) in enumerate(chapters):
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
        out_path = output_dir / f"{idx + 1:02d}_{safe_title}.mp3"

        chapter_total = len(split_text_into_chunks(content))

        def chapter_cb(current: int, total: int, msg: str) -> None:
            if progress_cb:
                # Map chapter progress into overall progress
                overall = idx * 100 + (current * 100 // max(total, 1))
                progress_cb(overall, total_chapters * 100, f"Luku {idx + 1}/{total_chapters}: {msg}")

        text_to_speech(content, out_path, config, chapter_cb if progress_cb else None)
        output_files.append(str(out_path))

    return output_files
