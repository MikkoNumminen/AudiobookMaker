"""Re-anchor ASR segment text to reference sentences.

ASR output is approximate — especially for rare words, proper nouns, and
domain-specific vocabulary common in books (place names, character names,
technical terms). Before we hand transcripts to the trainer as ground-truth
text we re-anchor each ASR segment to the closest-matching sentence from
the original reference text (for example, sentences extracted from the
source epub).

This module is pure Python: stdlib only, no external deps, no I/O.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from src.voice_pack.types import AsrSegment

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE_RE = re.compile(r"\s+")


def split_sentences(text: str) -> list[str]:
    """Split ``text`` into sentences on ``.``/``!``/``?`` + whitespace.

    Whitespace is collapsed before splitting so newlines, tabs, and
    multi-space gaps don't produce phantom empty sentences. Sentence-final
    punctuation is preserved. Empty segments are dropped.

    If ``text`` contains no terminator at all, the whole (normalised)
    string is returned as a single sentence.

    >>> split_sentences("Hello world. How are you? Fine!")
    ['Hello world.', 'How are you?', 'Fine!']
    >>> split_sentences("just some text")
    ['just some text']
    >>> split_sentences("")
    []
    """
    if text is None:
        return []
    normalised = _WHITESPACE_RE.sub(" ", text).strip()
    if not normalised:
        return []
    parts = _SENTENCE_SPLIT_RE.split(normalised)
    return [p.strip() for p in parts if p.strip()]


def best_match(
    asr_text: str,
    candidates: list[str],
) -> tuple[int, float]:
    """Return ``(index, similarity)`` of the best candidate for ``asr_text``.

    Similarity is :class:`difflib.SequenceMatcher` ratio on lowercased,
    whitespace-stripped strings. Empty ``asr_text`` or empty
    ``candidates`` yields ``(-1, 0.0)``.

    >>> best_match("hello", ["hi", "hello", "bye"])
    (1, 1.0)
    >>> best_match("", ["a"])
    (-1, 0.0)
    >>> best_match("a", [])
    (-1, 0.0)
    """
    asr_norm = asr_text.strip().lower() if asr_text else ""
    if not asr_norm or not candidates:
        return (-1, 0.0)

    best_idx = -1
    best_ratio = 0.0
    for i, candidate in enumerate(candidates):
        cand_norm = candidate.strip().lower()
        if not cand_norm:
            continue
        ratio = SequenceMatcher(None, asr_norm, cand_norm).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = i
    if best_idx == -1:
        return (-1, 0.0)
    return (best_idx, best_ratio)


def _best_match_in_range(
    asr_text: str,
    candidates: list[str],
    start: int,
    stop: int,
) -> tuple[int, float]:
    """Like :func:`best_match` but restricted to ``candidates[start:stop]``.

    Returned index is the absolute index into ``candidates`` (not the
    slice-local index). Out-of-range inputs are clamped.
    """
    if not asr_text or not candidates:
        return (-1, 0.0)
    start = max(0, start)
    stop = min(len(candidates), stop)
    if start >= stop:
        return (-1, 0.0)
    sub = candidates[start:stop]
    rel_idx, ratio = best_match(asr_text, sub)
    if rel_idx < 0:
        return (-1, 0.0)
    return (start + rel_idx, ratio)


def realign(
    segments: list[AsrSegment],
    reference_text: str,
    *,
    min_similarity: float = 0.6,
    search_window: int = 50,
) -> list[AsrSegment]:
    """Replace each segment's text with its best reference sentence.

    For every :class:`AsrSegment` in ``segments`` we search ``reference_text``
    (split into sentences via :func:`split_sentences`) for the closest
    match. If similarity ``>= min_similarity`` we substitute the reference
    sentence, preserving its original case and punctuation. Otherwise the
    segment is returned unchanged.

    Timing (``start``, ``end``) and ``confidence`` are always preserved.
    Output length and order mirror the input.

    ``reference_text`` that is empty or whitespace-only short-circuits:
    the input list is returned unchanged (as a new list).

    ``search_window`` exploits the fact that ASR segments and book
    sentences share monotonic order: once we anchor segment ``N`` to
    sentence ``K``, segment ``N+1``'s match is usually near ``K``. We
    first search ``candidates[K-window : K+window+1]``, and fall back to
    a full scan only if nothing in that window clears ``min_similarity``.
    Pass ``search_window=0`` to always full-scan.
    """
    if not segments:
        return []

    if reference_text is None or not reference_text.strip():
        return list(segments)

    candidates = split_sentences(reference_text)
    if not candidates:
        return list(segments)

    result: list[AsrSegment] = []
    last_match_idx = 0
    has_anchor = False

    for seg in segments:
        idx = -1
        ratio = 0.0

        if search_window > 0 and has_anchor:
            window_start = max(0, last_match_idx - search_window)
            window_stop = min(len(candidates), last_match_idx + search_window + 1)
            idx, ratio = _best_match_in_range(
                seg.text, candidates, window_start, window_stop
            )
            if ratio < min_similarity:
                # Fall back to a full scan.
                idx, ratio = best_match(seg.text, candidates)
        else:
            idx, ratio = best_match(seg.text, candidates)

        if idx >= 0 and ratio >= min_similarity:
            replaced = AsrSegment(
                start=seg.start,
                end=seg.end,
                text=candidates[idx],
                confidence=seg.confidence,
            )
            result.append(replaced)
            last_match_idx = idx
            has_anchor = True
        else:
            result.append(seg)

    return result
