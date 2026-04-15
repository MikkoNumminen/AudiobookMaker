"""Pure helpers for the 'create a sample first' GUI feature.

A sample is a short MP3 the user can generate before committing to a
long full-book run. These helpers compute the sample text snippet and
the sample output file path. They have no GUI dependencies so they can
be unit-tested in isolation.
"""
from __future__ import annotations

from pathlib import Path

# 500 chars of typical Finnish/English prose ≈ 30 s of synthesized audio.
DEFAULT_SAMPLE_CHARS = 500


def extract_sample_text(text: str, max_chars: int = DEFAULT_SAMPLE_CHARS) -> str:
    """Return the first ~max_chars of text, trimmed at a sentence boundary.

    The truncation prefers ending on a `.`, `!`, or `?` so the listener
    hears a complete thought instead of mid-word silence. If no sentence
    boundary lands in the second half of the snippet, the snippet is
    returned as-is (rstripped).
    """
    text = text.strip()
    if len(text) <= max_chars:
        return text

    snippet = text[:max_chars]
    # Look for the latest sentence ender. Reject boundaries in the very
    # first 20% of the snippet — picking those would produce a sample
    # too short to be useful, even though it ends cleanly.
    cutoff_floor = max_chars // 5
    best = -1
    for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        idx = snippet.rfind(sep)
        if idx > best and idx >= cutoff_floor:
            best = idx + len(sep) - 1  # Keep the punctuation, drop the space.
    if best > 0:
        return snippet[: best + 1].strip()
    return snippet.rstrip()


def compute_sample_output_path(output_path: str) -> str:
    """Insert ``_sample`` before the file extension.

    Examples:
        ``kirja.mp3``       → ``kirja_sample.mp3``
        ``texttospeech_4.mp3`` → ``texttospeech_4_sample.mp3``
        ``C:/foo/bar.wav``  → ``C:/foo/bar_sample.wav``
    """
    p = Path(output_path)
    return str(p.with_name(f"{p.stem}_sample{p.suffix}"))
