"""PDF parsing module for AudiobookMaker.

Extracts text from PDF files, cleans it up, and detects chapters/sections.
Uses PyMuPDF (fitz) for reliable text extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF


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
    text = _remove_page_numbers(raw)
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
    r"|(?:\d+[\.\)]\s+\w)"  # "3. Something" or "3) Something"
    r"|(?:[IVXLC]+[\.\)]\s+\w)"  # Roman numerals: "IV. Something"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# Fallback: lines in ALL CAPS or title-case that are short (likely a heading)
_TITLE_CASE_RE = re.compile(r"^([A-ZÄÖÅ][a-zäöå]+(?: [A-ZÄÖÅ][a-zäöå]+){0,6})$")
_ALL_CAPS_RE = re.compile(r"^[A-ZÄÖÅ\s]{4,50}$")


def _looks_like_heading(line: str) -> bool:
    line = line.strip()
    if not line:
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


def parse_pdf(file_path: str | Path) -> ParsedBook:
    """Parse a PDF file into a ParsedBook.

    Args:
        file_path: Path to the PDF file.

    Returns:
        ParsedBook with metadata and chapters.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file cannot be opened as a PDF.
    """
    file_path = str(file_path)

    if not Path(file_path).exists():
        raise FileNotFoundError(f"PDF not found: {file_path}")

    try:
        doc = fitz.open(file_path)
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {file_path}") from exc

    metadata = _extract_metadata(doc, file_path)

    pages_text: list[tuple[int, str]] = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")  # plain text extraction
        if text.strip():
            pages_text.append((page_num + 1, text))

    doc.close()

    chapters = _split_into_chapters(pages_text)

    return ParsedBook(metadata=metadata, chapters=chapters)
