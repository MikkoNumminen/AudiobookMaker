"""Text chunking for TTS synthesis.

Extracted from ``src/tts_engine.py`` as part of the engine split. Splits
long texts into sentence-aligned chunks small enough for online synthesis
requests. Pure text in / list-of-strings out — no synthesis dependencies.
"""

from __future__ import annotations


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
