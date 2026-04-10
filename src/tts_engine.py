"""TTS engine module for AudiobookMaker.

Converts text to speech using edge-tts and combines audio chunks with pydub.
Supports Finnish and English voices with configurable speed.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import edge_tts
from pydub import AudioSegment


# ---------------------------------------------------------------------------
# Voice configuration
# ---------------------------------------------------------------------------

# voice_id -> display name shown in the GUI
VOICE_DISPLAY_NAMES: dict[str, str] = {
    "fi-FI-NooraNeural": "Noora (suomi, nainen)",
    "fi-FI-HarriNeural": "Harri (suomi, mies)",
    "en-US-JennyNeural": "Jenny (English US, female)",
    "en-US-AriaNeural": "Aria (English US, female)",
    "en-US-AvaNeural": "Ava (English US, female)",
    "en-US-GuyNeural": "Guy (English US, male)",
    "en-US-AndrewNeural": "Andrew (English US, male)",
    "en-GB-SoniaNeural": "Sonia (English GB, female)",
    "en-GB-RyanNeural": "Ryan (English GB, male)",
}

VOICES: dict[str, dict[str, str]] = {
    "fi": {
        "default": "fi-FI-NooraNeural",
        "Noora (suomi, nainen)": "fi-FI-NooraNeural",
        "Harri (suomi, mies)": "fi-FI-HarriNeural",
    },
    "en": {
        "default": "en-US-JennyNeural",
        "Jenny (English US, female)": "en-US-JennyNeural",
        "Aria (English US, female)": "en-US-AriaNeural",
        "Ava (English US, female)": "en-US-AvaNeural",
        "Guy (English US, male)": "en-US-GuyNeural",
        "Andrew (English US, male)": "en-US-AndrewNeural",
        "Sonia (English GB, female)": "en-GB-SoniaNeural",
        "Ryan (English GB, male)": "en-GB-RyanNeural",
    },
}

# Maximum characters per TTS request. edge-tts has no hard limit but
# large chunks cause timeouts; 3000 chars is reliable in practice.
MAX_CHUNK_CHARS = 3000

# Sentence-ending punctuation used for smart splitting
_SENTENCE_END = {".", "!", "?", "…", "。"}


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

    def resolved_voice(self) -> str:
        if self.voice:
            return self.voice
        lang = self.language if self.language in VOICES else "fi"
        return VOICES[lang]["default"]


ProgressCallback = Callable[[int, int, str], None]
"""Callback(current_chunk, total_chunks, status_message)."""


# ---------------------------------------------------------------------------
# Text chunking
# ---------------------------------------------------------------------------


def split_text_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split text into chunks of at most max_chars characters.

    Splits on sentence boundaries when possible to avoid breaking mid-sentence.

    Args:
        text: Input text to split.
        max_chars: Maximum characters per chunk.

    Returns:
        List of text chunks.
    """
    if not text.strip():
        return []

    chunks: list[str] = []
    current = ""

    # Split into sentences by walking character by character
    sentences = _split_sentences(text)

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # A single sentence longer than max_chars must be force-split
        if len(sentence) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            # Force-split on word boundaries
            chunks.extend(_force_split(sentence, max_chars))
            continue

        if len(current) + len(sentence) + 1 <= max_chars:
            current = current + " " + sentence if current else sentence
        else:
            if current:
                chunks.append(current.strip())
            current = sentence

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if c]


def _split_sentences(text: str) -> list[str]:
    """Naive sentence splitter that preserves punctuation."""
    sentences: list[str] = []
    current = ""
    for char in text:
        current += char
        if char in _SENTENCE_END and len(current.strip()) > 1:
            sentences.append(current)
            current = ""
    if current.strip():
        sentences.append(current)
    return sentences


def _force_split(text: str, max_chars: int) -> list[str]:
    """Split a long string on word boundaries."""
    words = text.split()
    chunks: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 <= max_chars:
            current = current + " " + word if current else word
        else:
            if current:
                chunks.append(current)
            current = word
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# edge-tts async core
# ---------------------------------------------------------------------------


async def _synthesize_chunk(
    text: str,
    voice: str,
    rate: str,
    volume: str,
    output_path: str,
) -> None:
    """Synthesize a single chunk to an MP3 file via edge-tts."""
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=volume)
    await communicate.save(output_path)


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
# Audio combining
# ---------------------------------------------------------------------------


def combine_audio_files(input_paths: list[str], output_path: str) -> None:
    """Combine multiple MP3 files into one using pydub.

    Args:
        input_paths: Ordered list of MP3 file paths to concatenate.
        output_path: Destination MP3 path.

    Raises:
        ValueError: If input_paths is empty.
        RuntimeError: If audio combination fails.
    """
    if not input_paths:
        raise ValueError("No audio files to combine.")

    combined = AudioSegment.empty()
    for path in input_paths:
        segment = AudioSegment.from_mp3(path)
        combined += segment

    combined.export(output_path, format="mp3")


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
    chunks = split_text_into_chunks(text)

    if not chunks:
        raise ValueError("Text produced no chunks after splitting.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        mp3_files = asyncio.run(
            _synthesize_all_chunks(chunks, config, tmp_dir, progress_cb)
        )
        combine_audio_files(mp3_files, output_path)

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
