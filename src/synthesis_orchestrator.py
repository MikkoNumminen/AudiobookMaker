"""Synthesis orchestration — GUI-agnostic business logic.

This module owns the non-UI parts of turning a book into an audiobook:

- Book loading (dispatch to the right parser by extension)
- Output-path derivation and collision-bumping
- Default output directory resolution (frozen vs dev mode)

The GUI (``src/gui_unified.py``) is the only caller today, but the logic
here has zero tkinter, customtkinter, or widget dependencies — so it can
be unit-tested without spinning up a window, and a future CLI or web UI
can share the same code paths.

Later phases will extend the module to own in-process engine dispatch
(``_run_inprocess``) and subprocess dispatch (Chatterbox bridge) so
``UnifiedApp`` becomes a thin adapter. See ``docs/AUDIT_REPORT.md`` §1
"UnifiedApp is a god-object".
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from src.epub_parser import parse_epub
from src.pdf_parser import BookMetadata, Chapter, ParsedBook, parse_pdf


# ---------------------------------------------------------------------------
# Book loading
# ---------------------------------------------------------------------------


def parse_book(file_path: str) -> ParsedBook:
    """Route a book-shaped file to the right parser by extension.

    ``.pdf``  -> :func:`src.pdf_parser.parse_pdf`
    ``.epub`` -> :func:`src.epub_parser.parse_epub`
    ``.txt``  -> read UTF-8, wrap as a single-chapter ParsedBook

    Keeping the dispatcher in one place means every call site (conversion,
    preview, disk-space estimate) stays in sync when new formats are added.
    """
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return parse_pdf(file_path)
    if ext == ".epub":
        return parse_epub(file_path)
    if ext == ".txt":
        text = Path(file_path).read_text(encoding="utf-8", errors="replace")
        meta = BookMetadata(
            title=Path(file_path).stem.replace("_", " ").title(),
            author="",
            subject="",
            num_pages=1,
            file_path=str(file_path),
        )
        chapter = Chapter(
            title=meta.title or "Text",
            content=text,
            page_start=1,
            page_end=1,
            index=0,
        )
        return ParsedBook(metadata=meta, chapters=[chapter])
    # Unknown extension — default to PDF so legacy call sites still raise
    # the familiar error message.
    return parse_pdf(file_path)


# ---------------------------------------------------------------------------
# Output-path derivation
# ---------------------------------------------------------------------------


def default_output_dir() -> Path:
    """Return the default folder where generated MP3s go.

    Installed (frozen) mode: next to the running ``.exe`` (install root).
    Dev mode: ``Documents/AudiobookMaker`` (no sensible install root when
    running from source).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.home() / "Documents" / "AudiobookMaker"


def suggest_output_path(
    input_mode: str,
    pdf_path: str | None,
    out_dir: Path | None = None,
) -> str:
    """Return a default output path for a conversion.

    - ``input_mode == "pdf"`` with a path: sibling ``.mp3`` next to the book
      (``book.pdf`` -> ``book.mp3``).
    - Otherwise (text-paste mode or no path): auto-increment
      ``texttospeech_1.mp3``, ``texttospeech_2.mp3``, ... inside ``out_dir``
      (or :func:`default_output_dir` when ``out_dir`` is ``None``).

    The auto-increment scan stops at the first free slot — it does not
    skip gaps. So if ``texttospeech_3.mp3`` exists but ``_2`` doesn't, the
    caller gets ``_1`` back (or ``_2`` if ``_1`` also exists).

    The helper creates the output directory when it doesn't exist so the
    caller can immediately write to the returned path.
    """
    if input_mode == "pdf" and pdf_path:
        return str(Path(pdf_path).with_suffix(".mp3"))
    base_dir = out_dir if out_dir is not None else default_output_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        candidate = base_dir / f"texttospeech_{n}.mp3"
        if not candidate.exists():
            return str(candidate)
        n += 1


_TRAILING_NUMBER_RE = re.compile(r"^(.*?)_(\d+)$")


def next_available_numbered_path(output_path: str) -> str:
    """Bump ``output_path`` to the next free numbered sibling.

    When the file already exists, returns the lowest-numbered sibling that
    doesn't exist yet. The numbering rule mirrors what the GUI has always
    done:

    - ``texttospeech_3.mp3`` (exists) -> ``texttospeech_4.mp3``
    - ``book.mp3`` (exists)           -> ``book_2.mp3``
    - ``book_5.mp3`` (exists)         -> ``book_6.mp3``

    When the path does *not* exist, it is returned unchanged — no-op is
    cheap at the call site.
    """
    target = Path(output_path)
    if not target.exists():
        return output_path

    stem = target.stem
    suffix = target.suffix or ".mp3"
    parent = target.parent

    # Split trailing _N off the stem, defaulting to 1 if not numbered.
    match = _TRAILING_NUMBER_RE.match(stem)
    if match:
        base, n = match.group(1), int(match.group(2))
    else:
        base, n = stem, 1

    while True:
        n += 1
        candidate = parent / f"{base}_{n}{suffix}"
        if not candidate.exists():
            return str(candidate)
