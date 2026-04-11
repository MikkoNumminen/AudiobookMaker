"""TTS engine module for AudiobookMaker.

Converts text to speech using edge-tts and combines audio chunks with pydub.
Supports Finnish and English voices with configurable speed.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from pydub import AudioSegment

# NOTE: edge_tts is imported lazily inside _synthesize_chunk() so that
# other consumers (e.g. dev_qwen_tts.py) can `from src.tts_engine import
# split_text_into_chunks` without dragging in an online-TTS dependency
# they don't need.


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

# ---------------------------------------------------------------------------
# Finnish text normalization
# ---------------------------------------------------------------------------
#
# Raw PDF text often contains Finnish-specific patterns that TTS engines
# read poorly: bare years ("1500"), century expressions ("1500-luvulla"),
# numeric ranges, page abbreviations, decimals, and elided-hyphen compounds
# ("keski-ja" instead of "keski- ja"). Edge-TTS Noora handles some cases
# server-side but not all; Chatterbox-TTS has no number normalization at
# all. Normalizing before chunking benefits every engine we plug in.
#
# Passes run in a fixed order because earlier patterns must consume their
# digits before the generic "bare integer" fallback rewrites them. For
# example, "1500-luvulla" MUST be handled by pass C before pass G sees a
# loose 1500.

# Pass A: bibliographic citations — parens containing a 4-digit year and a
# Capitalized publisher-ish token. Conservative: requires BOTH.
_FI_CITE_RE = re.compile(
    r"\s*\(([^()]*?\b[A-ZÅÄÖ][\wäöåÄÖÅ]+[^()]*?\b\d{4}[a-z]?\b[^()]*?)\)"
)

# Pass B: elided-hyphen Finnish compounds (e.g. "keski-ja" → "keski- ja").
_FI_ELIDED_HYPHEN_RE = re.compile(
    r"(\w+)-(ja|tai|eli|sekä)\b", re.IGNORECASE
)

# Pass C: century/era expressions — digit + "-luku" declension suffix.
_FI_CENTURY_SUFFIXES = (
    "luvulla",
    "luvulta",
    "luvulle",
    "luvuilla",
    "luvusta",
    "luvut",
    "luvun",
    "luku",
)
_FI_CENTURY_RE = re.compile(
    r"(\d+)-(" + "|".join(_FI_CENTURY_SUFFIXES) + r")\b"
)

# Pass D: numeric ranges like "1500-1800" or "1100–1300".
_FI_RANGE_RE = re.compile(r"(\d{3,4})\s*[-–]\s*(\d{3,4})\b")

# Pass E: "s. 42" page abbreviation.
_FI_PAGE_RE = re.compile(r"\bs\.\s*(\d+)")

# Pass F: decimals (comma or dot separator).
_FI_DECIMAL_RE = re.compile(r"(\d+)[.,](\d+)")

# Pass G: any remaining bare integer.
_FI_INT_RE = re.compile(r"\d+")

# Pass H: split glued Finnish compound-number morphemes.
#
# num2words 0.5.14 emits Finnish compound numbers WITHOUT spaces between
# hundreds/tens/units morphemes — e.g. 1889 -> "tuhat
# kahdeksansataakahdeksankymmentäyhdeksän". Chatterbox-TTS then tokenizes
# the glued word as one giant token and mispronounces it. We insert a
# space after "sataa" (hundred, partitive form emitted by num2words for
# 200-900) and after "kymmentä" (ten, partitive) when another morpheme
# is glued on. Standalone teens like "viisitoista" (15) and "yksitoista"
# (11) are unaffected because they do not contain these morphemes.
_FI_MORPHEME_BOUNDARY_RE = re.compile(r"(sataa|kymmentä)(?=[a-zäöå])")


def _fi_split_number_compounds(text: str) -> str:
    """Insert spaces at morpheme boundaries in Finnish compound numbers.

    See :data:`_FI_MORPHEME_BOUNDARY_RE` for the rationale. Operates on
    already-normalized text (post num2words expansion).
    """
    return _FI_MORPHEME_BOUNDARY_RE.sub(r"\1 ", text)


def normalize_finnish_text(text: str, drop_citations: bool = True) -> str:
    """Expand Finnish-specific patterns so TTS engines read them correctly.

    Rewrites numbers, century expressions, numeric ranges, page abbreviations,
    and elided-hyphen compounds into plain word-form Finnish. Uses num2words
    (lazy import) for the actual digit → word conversion; if the package is
    not installed the function degrades gracefully and returns the input
    unchanged.

    Args:
        text: Raw Finnish text.
        drop_citations: If True, strip bibliographic citations like
            "(Pihlajamäki 2005)" — they are distracting when read aloud.

    Returns:
        Normalized text ready for TTS synthesis.
    """
    if not text:
        return text
    try:
        from num2words import num2words  # type: ignore
    except ImportError:
        return text

    def _w(n: int) -> str:
        try:
            return num2words(n, lang="fi")
        except (NotImplementedError, OverflowError, ValueError):
            return str(n)

    # Pass A — drop bibliographic citations.
    if drop_citations:
        text = _FI_CITE_RE.sub("", text)

    # Pass B — elided-hyphen compounds (just insert a space).
    text = _FI_ELIDED_HYPHEN_RE.sub(r"\1- \2", text)

    # Pass C — century expressions.
    def _century_sub(m: re.Match) -> str:
        return f"{_w(int(m.group(1)))} {m.group(2)}"

    text = _FI_CENTURY_RE.sub(_century_sub, text)

    # Pass D — numeric ranges (must run before decimals/bare ints).
    def _range_sub(m: re.Match) -> str:
        return f"{_w(int(m.group(1)))} {_w(int(m.group(2)))}"

    text = _FI_RANGE_RE.sub(_range_sub, text)

    # Pass E — "s. 42" page abbreviation.
    def _page_sub(m: re.Match) -> str:
        return f"sivu {_w(int(m.group(1)))}"

    text = _FI_PAGE_RE.sub(_page_sub, text)

    # Pass F — decimals.
    def _decimal_sub(m: re.Match) -> str:
        whole = int(m.group(1))
        frac_str = m.group(2)
        try:
            return num2words(float(f"{whole}.{frac_str}"), lang="fi")
        except (NotImplementedError, ValueError):
            return f"{_w(whole)} pilkku {' '.join(_w(int(d)) for d in frac_str)}"

    text = _FI_DECIMAL_RE.sub(_decimal_sub, text)

    # Pass G — any remaining bare integers.
    def _int_sub(m: re.Match) -> str:
        return _w(int(m.group(0)))

    text = _FI_INT_RE.sub(_int_sub, text)

    # Pass H — split glued compound-number morphemes (post num2words).
    text = _fi_split_number_compounds(text)

    # Collapse whitespace introduced by deletions/substitutions.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([.,;:!?])", r"\1", text)
    return text


# Maximum characters per TTS request. edge-tts has no hard limit but
# large chunks cause timeouts; 3000 chars is reliable in practice.
MAX_CHUNK_CHARS = 3000

# Sentence-ending punctuation used for smart splitting
_SENTENCE_END = {".", "!", "?", "…", "。"}

# Common Finnish/English abbreviations that end in a period but do NOT
# mark the end of a sentence. Matched case-insensitively on the token
# immediately before the period.
_ABBREVIATIONS = {
    # Finnish
    "esim", "ks", "mm", "ym", "yms", "n", "s", "v", "ts", "eli",
    "nk", "ns", "ko", "ao", "ed", "jne", "tms", "vrt", "huom",
    "mr", "mrs", "prof", "tri", "fil", "dos", "toim",
    # English
    "etc", "ie", "eg", "mr", "mrs", "ms", "dr", "vs", "cf", "no",
    "vol", "pp", "p", "ch", "fig", "ed", "al",
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
    """Split text into sentences, preserving punctuation.

    Handles the hard cases that a naive split-on-period-loses-to:
      * Abbreviations ("esim.", "ks.", "Mr.", "Dr.") — period does not end
        the sentence.
      * Numbered items and decimals ("1100-luvun", "5.2", "I.") — period
        followed by a digit or letter on the same token is not a sentence end.
      * Ellipsis ("...") — treated as a single terminator, not three.
      * A period is only a real sentence end when followed by whitespace and
        then an uppercase letter, digit-uppercase combination, or end of text.
    """
    if not text:
        return []

    sentences: list[str] = []
    n = len(text)
    start = 0
    i = 0
    while i < n:
        char = text[i]

        # Always treat ! and ? and … and 。 as hard sentence enders when
        # followed by whitespace or end of text.
        if char in {"!", "?", "…", "。"}:
            # Consume repeated punctuation (e.g. "?!").
            while i + 1 < n and text[i + 1] in {"!", "?", "…", "."}:
                i += 1
            if i + 1 >= n or text[i + 1].isspace():
                sentences.append(text[start : i + 1])
                i += 1
                # Skip whitespace
                while i < n and text[i].isspace():
                    i += 1
                start = i
                continue

        if char == ".":
            # Handle ellipsis "..."
            if i + 2 < n and text[i + 1] == "." and text[i + 2] == ".":
                i += 3
                if i >= n or text[i].isspace():
                    sentences.append(text[start:i])
                    while i < n and text[i].isspace():
                        i += 1
                    start = i
                    continue
                else:
                    continue

            # Look back at the token immediately before the period.
            token_start = i - 1
            while token_start >= start and not text[token_start].isspace():
                token_start -= 1
            token = text[token_start + 1 : i].lower()

            # Abbreviation?  Don't split.
            if token in _ABBREVIATIONS:
                i += 1
                continue

            # Single letter + period (initial like "H. Pihlajamäki") — don't split.
            if len(token) == 1 and token.isalpha():
                i += 1
                continue

            # Lookahead: is this really the end of a sentence?
            # A real sentence end is "."  followed by whitespace and then
            # an uppercase letter or a digit, or end of text.
            j = i + 1
            if j >= n:
                sentences.append(text[start : i + 1])
                start = n
                i = n
                break
            if not text[j].isspace():
                # e.g. "5.2" or "google.com" — not a sentence end.
                i += 1
                continue
            # Skip whitespace to find the next non-space character.
            k = j
            while k < n and text[k].isspace():
                k += 1
            if k >= n:
                sentences.append(text[start : i + 1])
                start = n
                i = n
                break
            # Accept if next char starts a new sentence-like token.
            if text[k].isupper() or text[k].isdigit() or text[k] in {'"', "'", "«", "("}:
                sentences.append(text[start : i + 1])
                i = k
                start = k
                continue
            # Otherwise (lowercase continuation) treat as inline period.
            i += 1
            continue

        i += 1

    if start < n:
        tail = text[start:]
        if tail.strip():
            sentences.append(tail)

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
    import edge_tts  # lazy import — see note at top of file

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


def _trim_chunk_silence(
    segment: AudioSegment,
    threshold_db: float = -45.0,
    keep_ms: int = 30,
) -> AudioSegment:
    """Trim leading and trailing silence from a synthesized chunk.

    edge-tts returns each chunk with ~150ms leading and ~800ms trailing
    silence.  Without trimming, concatenating 100+ chunks produces ~1 second
    of dead air at every chunk boundary, which sounds like the voice is
    cutting mid-sentence.

    Args:
        segment: Audio segment to trim.
        threshold_db: Anything quieter than this is considered silence.
        keep_ms: Amount of silence to keep at each edge for a natural edge.

    Returns:
        Trimmed audio segment.
    """
    from pydub.silence import detect_leading_silence

    lead = detect_leading_silence(segment, silence_threshold=threshold_db)
    trail = detect_leading_silence(segment.reverse(), silence_threshold=threshold_db)
    start = max(0, lead - keep_ms)
    end = len(segment) - max(0, trail - keep_ms)
    if end <= start:
        # Chunk was entirely silent — return it as-is to avoid a zero-length slice
        return segment
    return segment[start:end]


def combine_audio_files(
    input_paths: list[str],
    output_path: str,
    inter_chunk_pause_ms: int = 200,
) -> None:
    """Combine multiple MP3 files into one using pydub.

    Each chunk is trimmed of leading/trailing silence before concatenation so
    that the seams between chunks don't sound like dead air.  A short natural
    pause is inserted between chunks so the speech flow still feels paced.

    Args:
        input_paths: Ordered list of MP3 file paths to concatenate.
        output_path: Destination MP3 path.
        inter_chunk_pause_ms: Length of the synthetic pause inserted between
            adjacent chunks (milliseconds).

    Raises:
        ValueError: If input_paths is empty.
    """
    if not input_paths:
        raise ValueError("No audio files to combine.")

    gap = AudioSegment.silent(duration=inter_chunk_pause_ms)
    combined = AudioSegment.empty()
    for i, path in enumerate(input_paths):
        # Auto-detect format from the file extension so both edge-tts MP3
        # chunks and piper WAV chunks are supported.
        segment = _trim_chunk_silence(AudioSegment.from_file(path))
        combined += segment
        if i < len(input_paths) - 1:
            combined += gap

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

    if config.normalize_text and config.language == "fi":
        text = normalize_finnish_text(text)

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
