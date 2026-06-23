"""Text chunking for TTS synthesis.

Extracted from ``src/tts_engine.py`` as part of the engine split. Splits
long texts into sentence-aligned chunks small enough for online synthesis
requests. Pure text in / list-of-strings out — no synthesis dependencies.
"""

from __future__ import annotations

import re


# Clause-boundary punctuation. When a single sentence is too long to fit in a
# chunk it MUST be split, but splitting mid-phrase inserts an audible pause
# there (each chunk seam carries a VAD tail-pad + inter-chunk gap). Prefer to
# break right after a comma / semicolon / colon / dash — a place the listener
# already expects a short pause. The split keeps the punctuation on the
# left-hand clause.
_CLAUSE_BOUNDARY_RE = re.compile(r"(?<=[,;:—–])\s+")


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



def split_text_into_chunks(
    text: str, max_chars: int = MAX_CHUNK_CHARS, min_chars: int = 0
) -> list[str]:
    """Split text into chunks, each at most max_chars characters.

    Splits on sentence boundaries when possible to avoid breaking mid-sentence.
    (A sub-``min_chars`` fragment may be folded into a neighbor and so slightly
    exceed ``max_chars`` — see ``min_chars`` below.)

    Args:
        text: Input text to split.
        max_chars: Maximum characters per chunk.
        min_chars: If > 0, fold any chunk shorter than this into a neighbor
            (see :func:`_merge_short_chunks`). Tiny chunks — a stray clause
            like ``"vuoksi,"`` left over from splitting a long sentence — make
            some neural TTS models behave pathologically (Chatterbox rambles
            for 10+ seconds on a 7-char input). Off by default so the online
            engines (which chunk at 3000 chars and never see fragments this
            small) are unaffected; the Chatterbox runner opts in.

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

    return _merge_short_chunks([c for c in chunks if c], max_chars, min_chars)


def _merge_short_chunks(
    chunks: list[str], max_chars: int, min_chars: int
) -> list[str]:
    """Fold sub-``min_chars`` chunks into a neighbor.

    A tiny chunk — a stray clause like ``"vuoksi,"`` left over when a long
    sentence is force-split at commas — makes some neural TTS models misbehave:
    Chatterbox rambles for 10+ seconds on a 7-char input, and that audio
    survives VAD-trimming straight into the book as garbage. Merge any chunk
    shorter than ``min_chars`` into the preceding chunk, allowing a modest
    overflow past ``max_chars`` (a 230-char chunk reads fine; a 7-char one does
    not). A short chunk is only left standalone if folding it would exceed the
    overflow ceiling — rare, and the synth-side band guard catches the rest.
    """
    if min_chars <= 0 or len(chunks) <= 1:
        return chunks
    ceiling = max_chars + min_chars
    out: list[str] = []
    for ch in chunks:
        if (
            out
            and (len(out[-1]) < min_chars or len(ch) < min_chars)
            and len(out[-1]) + 1 + len(ch) <= ceiling
        ):
            out[-1] = out[-1] + " " + ch
        else:
            out.append(ch)
    return out


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
    """Split a too-long sentence, preferring clause boundaries over bare words.

    A sentence longer than max_chars must be broken, but a mid-phrase break
    puts an audible pause there. Pack clauses (split at commas/semicolons/
    colons/dashes) up to max_chars; only fall back to word boundaries when a
    single clause is itself too long. This keeps the seam — and its pause — at
    a place the listener already expects one.
    """
    clauses = _CLAUSE_BOUNDARY_RE.split(text)
    chunks: list[str] = []
    current = ""
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        if len(clause) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_word_split(clause, max_chars))
            continue
        if len(current) + len(clause) + 1 <= max_chars:
            current = current + " " + clause if current else clause
        else:
            if current:
                chunks.append(current)
            current = clause
    if current:
        chunks.append(current)
    return chunks


def _word_split(text: str, max_chars: int) -> list[str]:
    """Last-resort split on word boundaries (a single clause exceeds max_chars).

    A single token longer than ``max_chars`` (a URL, a long compound word,
    run-together text with no spaces) has no word boundary to break on, so it
    is hard-split into ``max_chars``-sized pieces. Without this the oversized
    token was emitted as one chunk that violated the "each chunk at most
    max_chars" contract — a downstream TTS engine could then choke on it.
    """
    words = text.split()
    chunks: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 <= max_chars:
            current = current + " " + word if current else word
        else:
            if current:
                chunks.append(current)
                current = ""
            # Hard-split a single oversized word into max_chars pieces; the
            # final remainder (< max_chars, possibly empty) seeds ``current``
            # so the next word can pack onto it.
            while len(word) > max_chars:
                chunks.append(word[:max_chars])
                word = word[max_chars:]
            current = word
    if current:
        chunks.append(current)
    return chunks
