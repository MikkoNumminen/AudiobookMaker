"""Pass S — acronym handling for the English TTS normalizer.

Spells out ALL-CAPS tokens letter-by-letter so the TTS engine says
"F B I" instead of trying to pronounce "FBI" as a word. A small
whitelist of pronounceable acronyms (NASA, NATO, LASER, ...) is left
alone because they are read as words in normal speech.

Rules:
    * Only tokens of 2-5 A-Z letters are considered.
    * Whitelist entries stay as-is.
    * Single letters (A, I) are not touched (regex won't match anyway).
    * If a token sits in the middle of a run of 3+ consecutive
      ALL-CAPS tokens, it is treated as part of a heading and left
      alone.
    * Already-spaced letter sequences (e.g. "F B I") never match the
      2-5 letter pattern in the first place, so they pass through.

The function is idempotent: once a token becomes "F B I", the
individual letters are single characters and no longer match the
2-5 letter token pattern.
"""

from __future__ import annotations

import functools
import re

__all__ = ["_pass_s_acronyms"]


# Pronounceable acronyms that should stay as a single word. The list
# lives in data/en_acronym_whitelist.yaml so non-developers can extend it.
@functools.lru_cache(maxsize=1)
def _load_whitelist() -> frozenset[str]:
    from src._yaml_data import load_yaml
    raw = load_yaml("en_acronym_whitelist") or []
    return frozenset(str(w) for w in raw)

# 2-5 uppercase letters, bounded by word boundaries.
_TOKEN_RE = re.compile(r"\b[A-Z]{2,5}\b")

# Used for the heading-run heuristic: a whitespace-separated token that
# is entirely uppercase letters (any length >= 2).
_ALLCAPS_NEIGHBOR_RE = re.compile(r"^[A-Z]{2,}$")


def _is_allcaps_neighbor(tok: str) -> bool:
    """Return True if ``tok`` looks like another ALL-CAPS word."""
    if not tok:
        return False
    # Strip trailing punctuation so "CHAPTER," still counts.
    stripped = tok.strip(".,:;!?\"'()[]{}")
    return bool(_ALLCAPS_NEIGHBOR_RE.match(stripped))


def _heading_run_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans for any run of 3+ consecutive
    ALL-CAPS (length >= 2) whitespace-separated tokens."""
    spans: list[tuple[int, int]] = []
    # Find all whitespace-separated tokens with their positions.
    tokens: list[tuple[int, int, str]] = []
    for m in re.finditer(r"\S+", text):
        tokens.append((m.start(), m.end(), m.group(0)))

    i = 0
    n = len(tokens)
    while i < n:
        if _is_allcaps_neighbor(tokens[i][2]):
            j = i
            while j < n and _is_allcaps_neighbor(tokens[j][2]):
                j += 1
            # Run is tokens[i..j-1]; length = j - i.
            if j - i >= 3:
                spans.append((tokens[i][0], tokens[j - 1][1]))
            i = j
        else:
            i += 1
    return spans


def _in_heading_run(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    for s, e in spans:
        if s <= start and end <= e:
            return True
    return False


def _pass_s_acronyms(text: str) -> str:
    """Spell out ALL-CAPS acronyms letter-by-letter.

    See module docstring for rules.
    """
    if not text:
        return text

    heading_spans = _heading_run_spans(text)
    whitelist = _load_whitelist()

    def _sub(m: re.Match[str]) -> str:
        tok = m.group(0)
        # Whitelist: pronounceable acronyms stay as single word.
        if tok in whitelist:
            return tok
        # Heading run: leave alone if token is inside a 3+ ALL-CAPS run.
        if _in_heading_run(heading_spans, m.start(), m.end()):
            return tok
        # Spell letter-by-letter.
        return " ".join(tok)

    return _TOKEN_RE.sub(_sub, text)
