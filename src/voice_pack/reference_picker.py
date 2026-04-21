"""Pick a clean ~15 s reference clip for one speaker.

The GUI clone-voice flow calls this after :mod:`scripts.voice_pack_analyze`
has produced ``transcripts.jsonl``. For each speaker the user wants to
ship as a few-shot voice pack we need a single short WAV that Chatterbox
can condition on at synthesis time. Quality of the pick matters — a
noisy, digit-laden, or overlap-bleeding clip hurts every subsequent
synthesis.

This module is pure logic: it takes the chunks already produced by
analyze plus a source WAV, and picks one chunk whose audio region will
be sliced out at 24 kHz mono. Everything about *how* to read and write
audio is dependency-injected so the unit tests need neither pydub nor
soundfile.

Heuristics (applied in order, each one only a soft preference unless
noted):

* **Speaker match.** Only chunks whose ``speaker`` equals the requested
  id. Hard filter — no fallback.
* **Duration window.** Chunks whose duration lies in
  ``[min_seconds, max_seconds]`` (default 12–18 s). Soft: if zero
  chunks fit, the nearest-duration chunks are kept and the report
  records a ``fallback_reason``.
* **Position.** Chunks starting within the first 5 s or ending within
  the last 5 s of the source are avoided (intro/outro are frequently
  music-bedded or spoken by a different voice). Soft.
* **Text quality.** Reject text containing digits (numerals mispronounce
  and contaminate the clone), 3+ consecutive uppercase letters
  (acronyms), fewer than 6 words, or more than 80 words. Soft.
* **RMS stability.** When an ``audio_reader`` is supplied we compute
  the standard deviation of per-200 ms RMS across each candidate clip
  and prefer low-variance clips (consistent volume). Without a reader
  this term is zero and the other heuristics decide on their own.

The picker returns a :class:`ReferenceClipReport` with the selected
start/end, a score, and — when relevant — a fallback-reason string the
GUI can surface to the user. The caller is expected to use the returned
``out_path`` directly; the writer has already put a 24 kHz mono WAV
there.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.voice_pack.types import VoiceChunk

# Per-speaker reference clip defaults. Tuned to Chatterbox's sweet spot:
# clips shorter than ~10 s starve the prosody encoder; clips longer than
# ~20 s bloat GPU memory at synthesis time without adding fidelity.
DEFAULT_MIN_SECONDS: float = 12.0
DEFAULT_MAX_SECONDS: float = 18.0

# Seconds from each end of the source audio to avoid. Intros frequently
# carry publisher boilerplate; outros carry credits / music. Both hurt
# the clone.
EDGE_GUARD_SECONDS: float = 5.0

# Target sample rate for the written reference WAV. Matches the rest of
# the voice-pack pipeline and the existing `reference_finnish.wav`.
REFERENCE_SAMPLE_RATE_HZ: int = 24000

# Text-quality filters.
_DIGIT_RE = re.compile(r"\d")
_ACRONYM_RE = re.compile(r"[A-ZÄÖÅ]{3,}")
MIN_WORDS: int = 6
MAX_WORDS: int = 80

# RMS analysis window (seconds). At 24 kHz this is 4800 samples per
# window — coarse enough to not care about individual syllables, fine
# enough to catch volume swings within a sentence.
_RMS_WINDOW_S: float = 0.2


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceClipCandidate:
    """A scored single-chunk candidate.

    Kept internal to the module in normal use but exposed for tests and
    for a future "show candidates" UI when we want operator override.
    """

    chunk_index: int
    start: float
    end: float
    duration: float
    score: float
    rms_std: float
    text_preview: str
    penalties: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReferenceClipReport:
    """Report returned by :func:`pick_reference_clip`.

    ``fallback_reason`` is ``None`` when a chunk passed every soft
    preference cleanly. Otherwise it's a short human-readable string the
    GUI can surface (e.g. ``"no chunks in 12–18s window; picked nearest"``).
    """

    speaker: str
    selected_start: float
    selected_end: float
    selected_duration: float
    selected_score: float
    candidate_count: int
    out_path: Path
    fallback_reason: Optional[str] = None
    candidates: tuple[ReferenceClipCandidate, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Injectable I/O
# ---------------------------------------------------------------------------

# Read a mono float32 array for [start, end] of the source. Tests pass a
# fake; production uses :func:`_default_audio_reader` which depends on
# pydub.
AudioReader = Callable[[Path, float, float], "list[float]"]

# Write a 24 kHz mono WAV covering [start, end] to out_path. Tests pass a
# recording fake; production uses :func:`_default_audio_writer`.
AudioWriter = Callable[[Path, float, float, Path], None]


def _default_audio_reader(src: Path, start_s: float, end_s: float) -> "list[float]":
    """Read a mono float32 array covering ``[start_s, end_s)`` of ``src``.

    Uses pydub (already a dependency via ``voice_pack_dataset``) so we do
    not pull in soundfile/scipy for a single clip slice. Imported lazily
    so unit tests can skip the pydub import entirely.
    """
    from pydub import AudioSegment  # type: ignore[import-not-found]

    audio = AudioSegment.from_file(str(src))
    clip = audio[int(start_s * 1000) : int(end_s * 1000)]
    clip = clip.set_channels(1).set_frame_rate(REFERENCE_SAMPLE_RATE_HZ)
    # AudioSegment stores integer samples; convert to float in [-1, 1].
    samples = clip.get_array_of_samples()
    max_abs = float(1 << (clip.sample_width * 8 - 1))
    return [s / max_abs for s in samples]


def _default_audio_writer(
    src: Path, start_s: float, end_s: float, out_path: Path
) -> None:
    """Write a 24 kHz mono WAV of ``[start_s, end_s)`` to ``out_path``."""
    from pydub import AudioSegment  # type: ignore[import-not-found]

    audio = AudioSegment.from_file(str(src))
    clip = audio[int(start_s * 1000) : int(end_s * 1000)]
    clip = clip.set_channels(1).set_frame_rate(REFERENCE_SAMPLE_RATE_HZ)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    clip.export(str(out_path), format="wav")


# ---------------------------------------------------------------------------
# Pure-logic helpers (tested directly)
# ---------------------------------------------------------------------------


def load_transcripts(path: Path) -> list[VoiceChunk]:
    """Load every :class:`VoiceChunk` from a transcripts.jsonl file.

    Mirrors the loader in ``scripts/voice_pack_export.py`` but kept local
    to avoid importing the CLI module (which pulls in ffmpeg setup).
    """
    chunks: list[VoiceChunk] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                character = obj.get("character")
                chunks.append(
                    VoiceChunk(
                        start=float(obj["start"]),
                        end=float(obj["end"]),
                        text=str(obj["text"]),
                        speaker=str(obj["speaker"]),
                        confidence=float(obj["confidence"]),
                        character=str(character) if character is not None else None,
                    )
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"{path}:{line_no}: could not parse VoiceChunk row — {exc}"
                ) from exc
    return chunks


def _text_penalties(text: str) -> tuple[str, ...]:
    """Return a tuple of penalty tags for problematic text."""
    penalties: list[str] = []
    if _DIGIT_RE.search(text):
        penalties.append("digits")
    if _ACRONYM_RE.search(text):
        penalties.append("acronym")
    word_count = len(text.split())
    if word_count < MIN_WORDS:
        penalties.append("too_few_words")
    elif word_count > MAX_WORDS:
        penalties.append("too_many_words")
    return tuple(penalties)


def _duration_penalty(
    duration: float, min_s: float, max_s: float
) -> tuple[float, tuple[str, ...]]:
    """Return a scalar penalty and penalty tags for duration fit.

    Inside the window the penalty is zero. Outside it's the number of
    seconds off — so a 20 s chunk when max is 18 scores better than a
    25 s chunk.
    """
    if min_s <= duration <= max_s:
        return 0.0, ()
    if duration < min_s:
        return (min_s - duration), ("too_short",)
    return (duration - max_s), ("too_long",)


def _position_penalty(
    chunk_start: float,
    chunk_end: float,
    source_duration: float,
    guard_s: float,
) -> tuple[float, tuple[str, ...]]:
    """Return a scalar penalty for chunks near intro/outro."""
    penalties: list[str] = []
    penalty = 0.0
    if chunk_start < guard_s:
        penalties.append("intro")
        penalty += guard_s - chunk_start
    if chunk_end > source_duration - guard_s:
        penalties.append("outro")
        penalty += chunk_end - (source_duration - guard_s)
    return penalty, tuple(penalties)


def _rms_std(samples: "list[float]", sample_rate_hz: int) -> float:
    """Standard deviation of per-window RMS. Zero samples ⇒ returns 0.0.

    Implemented in pure Python to avoid pulling numpy into the picker's
    import path. For typical reference clip lengths (≤20 s at 24 kHz =
    480 000 samples, window 4800 ⇒ 100 windows) the constant factor is
    negligible.
    """
    if not samples:
        return 0.0
    window = max(1, int(_RMS_WINDOW_S * sample_rate_hz))
    windows_rms: list[float] = []
    for i in range(0, len(samples), window):
        chunk = samples[i : i + window]
        if not chunk:
            continue
        s = 0.0
        for x in chunk:
            s += x * x
        windows_rms.append((s / len(chunk)) ** 0.5)
    if len(windows_rms) < 2:
        return 0.0
    mean = sum(windows_rms) / len(windows_rms)
    var = sum((r - mean) ** 2 for r in windows_rms) / len(windows_rms)
    return var ** 0.5


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

# Weights balance the three soft-penalty terms. Tuned so that a chunk
# missing the duration window by a couple seconds is still preferable to
# a chunk with digits in the text. If this turns out to need retuning in
# practice, the three numbers are the only knobs.
_W_DURATION: float = 1.0
_W_POSITION: float = 0.5
_W_TEXT: float = 3.0  # per penalty tag
_W_RMS: float = 2.0


def score_candidate(
    chunk: VoiceChunk,
    source_duration: float,
    *,
    min_seconds: float,
    max_seconds: float,
    rms_std: float = 0.0,
) -> tuple[float, tuple[str, ...]]:
    """Score one chunk. Lower is better. Returns (score, penalty_tags).

    Pulled out of :func:`pick_reference_clip` so tests can validate the
    heuristics without setting up audio I/O.
    """
    dp, d_tags = _duration_penalty(chunk.duration, min_seconds, max_seconds)
    pp, p_tags = _position_penalty(
        chunk.start, chunk.end, source_duration, EDGE_GUARD_SECONDS
    )
    t_tags = _text_penalties(chunk.text)

    score = (
        _W_DURATION * dp
        + _W_POSITION * pp
        + _W_TEXT * len(t_tags)
        + _W_RMS * rms_std
    )
    return score, d_tags + p_tags + t_tags


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def pick_reference_clip(
    transcripts: Path,
    speaker_id: str,
    wav_source: Path,
    out_path: Path,
    *,
    min_seconds: float = DEFAULT_MIN_SECONDS,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    audio_reader: Optional[AudioReader] = None,
    audio_writer: Optional[AudioWriter] = None,
    source_duration: Optional[float] = None,
    top_k: int = 3,
) -> ReferenceClipReport:
    """Pick one short clip for ``speaker_id`` and write it to ``out_path``.

    ``audio_reader`` (optional) is called on the top-K by-metadata
    candidates to compute RMS stability as a tiebreaker. When ``None``
    RMS scoring is skipped — the pick still works, just with one fewer
    quality signal.

    ``audio_writer`` defaults to the pydub-based writer; tests override
    it with a recording fake.

    ``source_duration`` defaults to the ``end`` of the last chunk in the
    transcripts, which is a cheap proxy for the source WAV length.
    """
    audio_writer = audio_writer or _default_audio_writer

    all_chunks = load_transcripts(transcripts)
    speaker_chunks = [
        (i, c) for i, c in enumerate(all_chunks) if c.speaker == speaker_id
    ]
    if not speaker_chunks:
        raise ValueError(
            f"no chunks for speaker {speaker_id!r} in {transcripts}"
        )

    if source_duration is None:
        source_duration = max(c.end for c in all_chunks)

    # First pass — score every speaker chunk by metadata alone.
    metadata_scored: list[ReferenceClipCandidate] = []
    for idx, chunk in speaker_chunks:
        score, tags = score_candidate(
            chunk,
            source_duration,
            min_seconds=min_seconds,
            max_seconds=max_seconds,
            rms_std=0.0,
        )
        metadata_scored.append(
            ReferenceClipCandidate(
                chunk_index=idx,
                start=chunk.start,
                end=chunk.end,
                duration=chunk.duration,
                score=score,
                rms_std=0.0,
                text_preview=chunk.text[:80],
                penalties=tags,
            )
        )

    metadata_scored.sort(key=lambda c: c.score)

    # Optional RMS refinement — only on the top-K by metadata so we do
    # not decode 500 clips on a 10-minute source.
    refined: list[ReferenceClipCandidate] = []
    if audio_reader is not None:
        for cand in metadata_scored[:top_k]:
            samples = audio_reader(wav_source, cand.start, cand.end)
            rms = _rms_std(samples, REFERENCE_SAMPLE_RATE_HZ)
            # Rescore with RMS term included.
            chunk = all_chunks[cand.chunk_index]
            new_score, tags = score_candidate(
                chunk,
                source_duration,
                min_seconds=min_seconds,
                max_seconds=max_seconds,
                rms_std=rms,
            )
            refined.append(
                ReferenceClipCandidate(
                    chunk_index=cand.chunk_index,
                    start=cand.start,
                    end=cand.end,
                    duration=cand.duration,
                    score=new_score,
                    rms_std=rms,
                    text_preview=cand.text_preview,
                    penalties=tags,
                )
            )
        refined.sort(key=lambda c: c.score)
        winner = refined[0]
        final_candidates = tuple(refined)
    else:
        winner = metadata_scored[0]
        final_candidates = tuple(metadata_scored[:top_k])

    fallback_reason = _derive_fallback_reason(
        winner, min_seconds=min_seconds, max_seconds=max_seconds
    )

    audio_writer(wav_source, winner.start, winner.end, out_path)

    return ReferenceClipReport(
        speaker=speaker_id,
        selected_start=winner.start,
        selected_end=winner.end,
        selected_duration=winner.duration,
        selected_score=winner.score,
        candidate_count=len(speaker_chunks),
        out_path=out_path,
        fallback_reason=fallback_reason,
        candidates=final_candidates,
    )


def _derive_fallback_reason(
    winner: ReferenceClipCandidate,
    *,
    min_seconds: float,
    max_seconds: float,
) -> Optional[str]:
    """Turn the winning candidate's penalty tags into a user-facing note.

    Returns ``None`` when the pick is clean. The string is kept short
    and plain so the GUI can show it verbatim in Barney-style copy
    without localisation gymnastics.
    """
    if not winner.penalties:
        return None
    notes: list[str] = []
    if "too_short" in winner.penalties or "too_long" in winner.penalties:
        notes.append(
            f"no chunks fit the {min_seconds:.0f}-{max_seconds:.0f}s window; "
            f"picked a {winner.duration:.1f}s chunk"
        )
    if "intro" in winner.penalties or "outro" in winner.penalties:
        notes.append("picked clip is near the start or end of the source")
    if "digits" in winner.penalties:
        notes.append("clip text contains digits")
    if "acronym" in winner.penalties:
        notes.append("clip text contains an acronym")
    if "too_few_words" in winner.penalties:
        notes.append("clip text is short")
    if "too_many_words" in winner.penalties:
        notes.append("clip text is unusually long")
    return "; ".join(notes) if notes else None
