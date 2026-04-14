"""EPUB parsing module for AudiobookMaker.

Extracts text from EPUB files, cleans it up, and detects chapters.
Designed as a drop-in sibling of ``src.pdf_parser`` — the returned
:class:`~src.pdf_parser.ParsedBook` is the exact same dataclass so
downstream code (chunking, TTS) does not care which format the book
came from.

Strategy:
    1. Open the archive with ``ebooklib.epub.read_epub``.
    2. Walk every ``ITEM_DOCUMENT`` (XHTML) item in spine order.
    3. Extract plain text with BeautifulSoup ``get_text(separator=' ')``.
    4. Skip items under :data:`_MIN_ITEM_CHARS` — those are typically
       front-matter boilerplate (title page, half-title, blank pages)
       and add no listening value.
    5. Detect a chapter title from ``<h1>`` / ``<h2>`` / ``<title>`` /
       file-name stem in that order.
    6. Re-use ``pdf_parser.clean_text`` so the paragraph/whitespace
       rules stay consistent across formats.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ebooklib import ITEM_DOCUMENT, epub
from bs4 import BeautifulSoup

from src.pdf_parser import (
    BookMetadata,
    Chapter,
    EmptyPDFError,
    ParsedBook,
    clean_text,
)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Items shorter than this after text extraction are dropped as front-matter
# noise (titlepage, half-title, dedication stubs). The Rubicon test file has
# several items at 0 / 30-ish chars.
_MIN_ITEM_CHARS = 50


class EmptyEPUBError(EmptyPDFError):
    """Raised when an EPUB contains no extractable text."""

    pass


# ---------------------------------------------------------------------------
# Title detection
# ---------------------------------------------------------------------------


def _extract_title(soup: BeautifulSoup, file_name: str) -> str:
    """Pick the most chapter-like heading we can find.

    Preference order: ``<h1>`` -> ``<h2>`` -> ``<title>`` -> cleaned
    file-name stem. Falls back to ``"Chapter"`` only if everything else
    is empty or whitespace.
    """
    for tag in ("h1", "h2", "h3"):
        node = soup.find(tag)
        if node:
            text = node.get_text(separator=" ", strip=True)
            if text:
                # Normalize the replacement character that shows up when
                # Windows smart quotes were stored in cp1252 inside an
                # otherwise-UTF8 file. We just drop them — the heading is
                # cosmetic, not content.
                text = text.replace("\ufffd", "")
                if text.strip():
                    return text.strip()

    title_tag = soup.find("title")
    if title_tag:
        text = title_tag.get_text(strip=True).replace("\ufffd", "")
        if text and len(text) < 120:
            return text

    stem = Path(file_name).stem
    # File names are often "OEBPS/Holl_9780307427519_epub_c01_r1" —
    # strip the long publisher prefix when we can.
    return stem.replace("_", " ").strip() or "Chapter"


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def _first_metadata(book: epub.EpubBook, field: str) -> str:
    try:
        values = book.get_metadata("DC", field)
    except Exception:
        return ""
    if not values:
        return ""
    # Each entry is (value, attrs). Grab the string value only.
    first = values[0]
    if isinstance(first, tuple) and first:
        return (first[0] or "").strip()
    return str(first).strip()


def _extract_metadata(book: epub.EpubBook, file_path: str, num_items: int) -> BookMetadata:
    title = _first_metadata(book, "title")
    author = _first_metadata(book, "creator")
    subject = _first_metadata(book, "subject")

    if not title:
        title = Path(file_path).stem.replace("_", " ").title()

    return BookMetadata(
        title=title,
        author=author,
        subject=subject,
        # ``num_pages`` is semantically wrong for EPUB (no fixed pages),
        # but the field is reused as "item count" so the progress UI has
        # something to show. Name stays for drop-in compatibility.
        num_pages=num_items,
        file_path=file_path,
    )


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def _decode_item(item: epub.EpubItem) -> str:
    """Return the item's HTML content as a ``str``.

    ``ebooklib`` hands us bytes. The XHTML inside an EPUB is normally
    UTF-8, but some sloppy publishers mix in cp1252 bytes. ``errors=
    'replace'`` means those become U+FFFD; :func:`_extract_title` and the
    cleaning pipeline strip those replacement chars so the audio never
    reads "question mark" for a smart quote.
    """
    raw = item.get_content()
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def parse_epub(file_path: str | Path) -> ParsedBook:
    """Parse an EPUB file into a :class:`ParsedBook`.

    Mirrors :func:`src.pdf_parser.parse_pdf` so either parser can feed
    the same TTS pipeline.

    Args:
        file_path: Path to the ``.epub`` archive.

    Raises:
        FileNotFoundError: The file does not exist.
        ValueError: The archive is unreadable as EPUB.
        EmptyEPUBError: The EPUB has no extractable text.
    """
    file_path = str(file_path)
    if not Path(file_path).exists():
        raise FileNotFoundError(f"EPUB not found: {file_path}")

    try:
        book = epub.read_epub(file_path)
    except Exception as exc:
        raise ValueError(f"Cannot open EPUB: {file_path}") from exc

    # Preserve spine order when possible — that's the authored reading
    # order. Fall back to whatever order ``get_items()`` yields if the
    # spine is missing (some older EPUB2 files).
    document_items: list[epub.EpubItem] = []
    seen_ids: set[str] = set()
    try:
        for entry in book.spine:
            item_id = entry[0] if isinstance(entry, tuple) else entry
            item = book.get_item_with_id(item_id)
            if item is not None and item.get_type() == ITEM_DOCUMENT:
                document_items.append(item)
                seen_ids.add(item.id)
    except Exception:
        pass

    # Append any documents not in the spine so we don't silently drop
    # valid content (rare, but happens in hand-crafted EPUBs).
    for item in book.get_items():
        if item.get_type() == ITEM_DOCUMENT and item.id not in seen_ids:
            document_items.append(item)

    chapters: list[Chapter] = []
    index = 0
    for item in document_items:
        html = _decode_item(item)
        soup = BeautifulSoup(html, "html.parser")
        # Drop script/style — their text would otherwise end up in the
        # audio as JavaScript or CSS gibberish.
        for noise in soup(["script", "style"]):
            noise.decompose()

        raw_text = soup.get_text(separator=" ", strip=True)
        # Scrub replacement chars (from mismatched encodings) before we
        # measure length — a document that is ONLY replacement chars is
        # junk, not content.
        raw_text = raw_text.replace("\ufffd", "")

        if len(raw_text) < _MIN_ITEM_CHARS:
            continue

        content = clean_text(raw_text)
        if not content.strip():
            continue

        title = _extract_title(soup, item.file_name or f"chapter_{index}")

        chapters.append(
            Chapter(
                title=title,
                content=content,
                # EPUBs have no pages; keep the fields populated so the
                # Chapter dataclass stays compatible with the PDF code path.
                page_start=index + 1,
                page_end=index + 1,
                index=index,
            )
        )
        index += 1

    metadata = _extract_metadata(book, file_path, len(document_items))
    parsed = ParsedBook(metadata=metadata, chapters=chapters)

    if parsed.total_chars == 0:
        raise EmptyEPUBError(
            f"EPUB contains no extractable text ({len(document_items)} items). "
            "The file may be image-only — try a different source."
        )
    return parsed
