"""PDF parsing module for AudiobookMaker.

Extracts text from PDF files, cleans it up, and detects chapters/sections.
Uses PyMuPDF (fitz) for reliable text extraction. Falls back to OCR via
ocrmypdf + Tesseract for image-only pages when those tools are available
(see src/ocr_path.py).
"""

from __future__ import annotations

import hashlib
import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

from src.tts_chunking import HEADING_MAX_CHARS as _MAX_HEADING_LEN

logger = logging.getLogger(__name__)


class EmptyPDFError(ValueError):
    """Raised when a PDF contains no extractable text (e.g. scanned pages)."""
    pass


@dataclass
class BookMetadata:
    """Metadata extracted from a PDF file."""

    title: str = ""
    author: str = ""
    subject: str = ""
    num_pages: int = 0
    file_path: str = ""


@dataclass
class Chapter:
    """A chapter or section extracted from a PDF."""

    title: str
    content: str
    page_start: int
    page_end: int
    index: int  # zero-based chapter index

    def __len__(self) -> int:
        return len(self.content)


@dataclass
class ParsedBook:
    """Full parsed representation of a PDF book."""

    metadata: BookMetadata
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        """Return entire book text as one string."""
        return "\n\n".join(ch.content for ch in self.chapters)

    @property
    def total_chars(self) -> int:
        return sum(len(ch.content) for ch in self.chapters)


# ---------------------------------------------------------------------------
# Text cleaning helpers
# ---------------------------------------------------------------------------

# Common page-number patterns: bare numbers, "- 12 -", "Page 12", "Sivu 12"
_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:page|sivu|s\.?|p\.?)?\s*\d+\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Lines that look like running headers/footers: short (≤ 60 chars), no sentence
# punctuation, repeated across many pages – we detect by shortness + no verb
_SHORT_LINE_RE = re.compile(r"^.{1,60}$")

# Excessive whitespace
_MULTI_BLANK_RE = re.compile(r"\n{3,}")

# Matches a single newline that is NOT part of a paragraph break (double newline).
# Used to flatten PDF line wraps inside a paragraph into spaces so edge-tts
# doesn't insert a pause on every line break.
_SINGLE_NEWLINE_RE = re.compile(r"(?<!\n)\n(?!\n)")

# Hyphenated line-break (word split across lines): "käsit-\ntely" → "käsittely"
# Soft hyphen (U+00AD) — typographic hint that a word *may* be broken here.
# These are invisible in most readers but appear in extracted PDF text.
# Always strip them, regardless of whether a newline follows.
_SOFT_HYPHEN_RE = re.compile(r"\u00ad\s*\n?\s*")

# Hard hyphen at end of line (possibly with trailing space).
# Word-wrap case: letter before the hyphen AND lowercase letter after = remove hyphen
#   "var- \nhaismoderni" -> "varhaismoderni"
# Compound case: digit before the hyphen = preserve the hyphen (e.g. "1200-luvulla")
# Also preserve when the continuation starts with uppercase or a digit.
_HYPHEN_BREAK_WORDWRAP_RE = re.compile(r"([a-zäöA-ZÄÖ])-[ \t]*\n\s*([a-zäö])")
_HYPHEN_BREAK_KEEP_RE = re.compile(r"(\w)-[ \t]*\n\s*([A-ZÄÖ0-9a-zäö])")


# Invisible zero-width / formatting characters that carry no spoken content
# but routinely sneak out of PDFs/EPUBs/DOCX: zero-width space, ZW non-joiner,
# ZW joiner, word joiner, and the BOM / ZW no-break space. Left in, they split
# a word for the chunker (a ZWSP between letters) or get read as nothing. Soft
# hyphen (U+00AD) is deliberately NOT here — _fix_hyphenation owns it, since
# stripping it needs the line-join semantics. Written with \u escapes so the
# source stays readable (these glyphs are invisible). Dropped outright.
_ZERO_WIDTH_RE = re.compile("[​‌‍⁠﻿]")

# Every Unicode space separator (category Zs) EXCEPT the ASCII space: NBSP
# (U+00A0), Ogham space, the en/em/thin/hair/figure family (U+2000-U+200A),
# narrow NBSP, medium-math space, and the ideographic space. Real documents are
# full of these (NBSP especially); the downstream passes only collapse literal
# ASCII spaces, so without this they would survive into the audio text.
_UNICODE_SPACE_RE = re.compile(
    "[   -   　]"
)


def _normalize_unicode_whitespace(text: str) -> str:
    """Fold exotic whitespace to plain ASCII so the rest of the pipeline (and
    the TTS engine) only ever see regular spaces and newlines.

    Runs first in :func:`clean_text` so the page-number, hyphenation and
    whitespace passes — which assume plain spaces/newlines — behave. A small
    set of regex passes (not a per-char ``unicodedata`` loop) keeps it cheap
    on book-length text.
    """
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.replace("\t", " ")
    # Unicode line (U+2028) and paragraph (U+2029) separators become real
    # newlines so the paragraph-break logic downstream applies to them too.
    text = text.replace(" ", "\n").replace(" ", "\n\n")
    text = _UNICODE_SPACE_RE.sub(" ", text)
    return text


def _remove_page_numbers(text: str) -> str:
    return _PAGE_NUMBER_RE.sub("", text)


def _fix_hyphenation(text: str) -> str:
    # 1. Strip soft hyphens entirely (they are typographic hints, not content).
    text = _SOFT_HYPHEN_RE.sub("", text)
    # 2. Word-wrap hyphens (letter-hyphen-lowercase) -> join without the hyphen.
    #    This must run before the KEEP rule so the word-wrap case is consumed first.
    text = _HYPHEN_BREAK_WORDWRAP_RE.sub(r"\1\2", text)
    # 3. Everything else (e.g. "1200-\nluvulla", "Austro-\nHungarian") -> keep hyphen,
    #    drop the newline.
    text = _HYPHEN_BREAK_KEEP_RE.sub(r"\1-\2", text)
    return text


def _normalize_whitespace(text: str) -> str:
    # Strip trailing spaces on each line first, so that collapsed single newlines
    # don't leave "word \nnext" -> "word  next" (double space).
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines)
    # Collapse 3+ consecutive newlines down to a paragraph break.
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    # Preserve paragraph breaks (double newlines) while flattening in-paragraph
    # line wraps to a single space.  Without this, edge-tts pauses at every
    # line break inside a sentence because the PDF's line-wrapping leaks into
    # the extracted text.
    text = _SINGLE_NEWLINE_RE.sub(" ", text)
    # Collapse any accidental double-spaces introduced by the substitution.
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def clean_text(raw: str) -> str:
    """Apply all cleaning steps to raw extracted text."""
    # Fold exotic whitespace / drop zero-width chars FIRST so the page-number,
    # hyphenation and whitespace passes (which assume plain spaces/newlines)
    # see normalised input — and so none of it reaches the TTS engine.
    text = _normalize_unicode_whitespace(raw)
    text = _remove_page_numbers(text)
    text = _fix_hyphenation(text)
    text = _normalize_whitespace(text)
    return text


# ---------------------------------------------------------------------------
# Chapter detection
# ---------------------------------------------------------------------------

# Heading patterns – Finnish and English
_CHAPTER_HEADING_RE = re.compile(
    r"^(?:"
    r"(?:Luku|Chapter|Osa|Part|Kapitel)\s+\d+"  # numbered: "Luku 3"
    r"|(?:\d{1,3}[\.\)]\s+\w)"  # "3. Something" or "3) Something" (max 3 digits: rules out years like "1500.")
    r"|(?:[IVXLC]+[\.\)]\s+\w)"  # Roman numerals: "IV. Something"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# Fallback: lines in ALL CAPS or title-case that are short (likely a heading)
_TITLE_CASE_RE = re.compile(r"^([A-ZÄÖÅ][a-zäöå]+(?: [A-ZÄÖÅ][a-zäöå]+){0,6})$")
_ALL_CAPS_RE = re.compile(r"^[A-ZÄÖÅ\s]{4,50}$")

# Upper bound on heading length. Real chapter titles are short; anything longer
# is almost certainly a line of prose that happens to start like a heading.
#
# Single-sourced with the chunker's heading detector (imported at the top of
# this module). The two used to declare 80 independently, which is a silent
# drift waiting to happen: they answer related questions about the same
# documents and would have diverged the first time either was tuned.


def _looks_like_heading(line: str) -> bool:
    """True when a line opens a new CHAPTER (this parser's question).

    Not the same question as ``tts_chunking.looks_like_heading``, and the two
    must not be conflated:

    * This one decides chapter SPLITTING. A match becomes ``Chapter.title``
      and is removed from the chapter body — so a heading matched here is
      never narrated at all, and separation comes from the chapter being its
      own MP3 with an inter-chapter gap between files.
    * The chunker's decides whether to PAUSE around a heading that stays in
      the text. It only ever sees the sub-headings this one did not match.

    Between them every heading is handled, by one route or the other. Change
    either and check what moves between the two: a heading that stops matching
    here starts being spoken with pauses instead of vanishing into a filename.
    """
    line = line.strip()
    if not line:
        return False
    # Reject anything that is too long to plausibly be a heading — real titles
    # are short, prose lines are long.
    if len(line) > _MAX_HEADING_LEN:
        return False
    # Reject lines that contain a sentence-ending period followed by more words,
    # e.g. "1500. Nämä jaot on tarkoitettu ..." — that's prose, not a title.
    # A legitimate numbered heading has at most one "." (the one after the number).
    if re.search(r"\w\.\s+\w.*\s+\w", line):
        # More than one word-gap after an internal period -> looks like prose.
        # But allow "3. Johdanto" (single word after the period).
        # Count words after the first period.
        after = line.split(".", 1)[1] if "." in line else ""
        if len(after.strip().split()) > 4:
            return False
    if _CHAPTER_HEADING_RE.match(line):
        return True
    if _ALL_CAPS_RE.match(line) and len(line) >= 4:
        return True
    return False


def _split_into_chapters(pages_text: list[tuple[int, str]]) -> list[Chapter]:
    """
    Split a list of (page_number, text) tuples into Chapter objects.

    Strategy:
    1. Walk lines; when a heading is detected start a new chapter.
    2. If no headings found, treat the entire book as one chapter.
    """
    chapters: list[Chapter] = []
    current_title = "Alkusanat"
    current_lines: list[str] = []
    current_page_start = 1
    chapter_index = 0

    def flush(page_end: int) -> None:
        nonlocal chapter_index
        content = clean_text("\n".join(current_lines))
        if content.strip():
            chapters.append(
                Chapter(
                    title=current_title,
                    content=content,
                    page_start=current_page_start,
                    page_end=page_end,
                    index=chapter_index,
                )
            )
            chapter_index += 1

    for page_num, page_text in pages_text:
        for line in page_text.splitlines():
            if _looks_like_heading(line):
                flush(page_num)
                current_title = line.strip()
                current_lines = []
                current_page_start = page_num
            else:
                current_lines.append(line)

    # flush last chapter
    if pages_text:
        flush(pages_text[-1][0])

    # If no chapters were detected, wrap everything in one
    if not chapters:
        all_text = clean_text(
            "\n".join(text for _, text in pages_text)
        )
        chapters = [
            Chapter(
                title="Kirja",
                content=all_text,
                page_start=1,
                page_end=pages_text[-1][0] if pages_text else 1,
                index=0,
            )
        ]

    return chapters


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def _extract_metadata(doc: fitz.Document, file_path: str) -> BookMetadata:
    """Pull metadata from PDF properties."""
    meta = doc.metadata or {}
    title = meta.get("title", "").strip()
    author = meta.get("author", "").strip()
    subject = meta.get("subject", "").strip()

    # Fallback: use filename as title
    if not title:
        title = Path(file_path).stem.replace("_", " ").replace("-", " ").title()

    return BookMetadata(
        title=title,
        author=author,
        subject=subject,
        num_pages=len(doc),
        file_path=file_path,
    )


def _extract_pages(doc) -> tuple[list[tuple[int, str]], int]:
    """Extract text from every page. Returns (pages_text, empty_page_count).

    Pages with no extractable text (typical for scanned / image-only PDFs)
    are not added to ``pages_text`` but are counted so the OCR-fallback
    decision in ``parse_pdf`` knows when to retry.
    """
    pages_text: list[tuple[int, str]] = []
    empty = 0
    for page_num in range(len(doc)):
        text = doc[page_num].get_text("text")
        if text.strip():
            pages_text.append((page_num + 1, text))
        else:
            empty += 1
    return pages_text, empty


def _sha256_file(path: str) -> str:
    """sha256 hex digest of a file's bytes. Used as a cache key for OCR
    output — identical source PDF means identical OCR'd PDF, so two runs
    over the same input skip re-running Tesseract."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _ocr_cache_path(source_path: str, cache_dir: Path | None) -> Path:
    """Resolve where the OCR'd PDF for ``source_path`` should live.

    With ``cache_dir`` given, the file lives there permanently. Without,
    it lands in the OS temp dir — still benefits from per-content
    de-dupe within a session, less aggressive across reboots.
    """
    digest = _sha256_file(source_path)
    if cache_dir is not None:
        return cache_dir / f"{digest}.pdf"
    return Path(tempfile.gettempdir()) / f"audiobookmaker_ocr_{digest[:16]}.pdf"


def _run_ocr_fallback(
    source_path: str,
    ocr_language: str,
    cache_dir: Path | None,
) -> Path | None:
    """Run ocrmypdf in --skip-text mode. Return the OCR'd PDF path, or None
    if OCR is unavailable / failed.

    Cached results are returned without re-running. ``ocr_language`` is the
    Tesseract code (e.g. "fin", "eng"); callers map TTS codes via
    ``ocr_path.tesseract_lang_for``.
    """
    from src.ocr_path import is_ocr_available

    if not is_ocr_available():
        logger.info(
            "OCR fallback skipped — ocrmypdf / Tesseract / Ghostscript "
            "not reachable. Install them, or rely on extracted text only."
        )
        return None

    output_path = _ocr_cache_path(source_path, cache_dir)
    if output_path.exists():
        logger.info("Using cached OCR'd PDF: %s", output_path)
        return output_path

    # Lazy import — the module-level import of pdf_parser must stay light
    # so callers without ocrmypdf in their env don't crash at import time.
    import ocrmypdf

    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)

    kwargs: dict = {"skip_text": True}
    if ocr_language:
        kwargs["language"] = ocr_language

    try:
        logger.info(
            "Running OCR on %s -> %s (lang=%s)",
            source_path, output_path, ocr_language or "auto",
        )
        ocrmypdf.ocr(source_path, str(output_path), **kwargs)
        return output_path
    except Exception as exc:
        logger.warning("OCR fallback failed: %s", exc)
        if output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass
        return None


def parse_pdf(
    file_path: str | Path,
    *,
    ocr_language: str = "",
    ocr_cache_dir: Path | None = None,
) -> ParsedBook:
    """Parse a PDF file into a ParsedBook.

    If any page yields no extractable text (image-only / scanned pages),
    and OCR tooling is available, automatically re-run extraction against
    an ocrmypdf-produced version that has a searchable text layer added
    where the source lacked one.

    Args:
        file_path: Path to the PDF file.
        ocr_language: Tesseract language code (e.g. "fin", "eng"). When
            empty, ocrmypdf picks its own default. Callers should pass
            the active TTS language via ``ocr_path.tesseract_lang_for``.
        ocr_cache_dir: When given, OCR'd PDFs are persisted here keyed
            by source-content hash. When None, the OS temp dir is used.

    Returns:
        ParsedBook with metadata and chapters.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file cannot be opened as a PDF.
        EmptyPDFError: If even after the OCR fallback the document
            contains no extractable text.
    """
    file_path = str(file_path)

    if not Path(file_path).exists():
        raise FileNotFoundError(f"PDF not found: {file_path}")

    try:
        doc = fitz.open(file_path)
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {file_path}") from exc

    metadata = _extract_metadata(doc, file_path)
    pages_text, empty_pages = _extract_pages(doc)
    doc.close()

    if empty_pages:
        ocr_pdf = _run_ocr_fallback(file_path, ocr_language, ocr_cache_dir)
        if ocr_pdf is not None:
            with fitz.open(str(ocr_pdf)) as ocr_doc:
                pages_text, _ = _extract_pages(ocr_doc)

    chapters = _split_into_chapters(pages_text)

    book = ParsedBook(metadata=metadata, chapters=chapters)
    if book.total_chars == 0:
        raise EmptyPDFError(
            f"PDF contains no extractable text ({metadata.num_pages} pages). "
            "The file may be scanned — try OCR first."
        )
    return book
