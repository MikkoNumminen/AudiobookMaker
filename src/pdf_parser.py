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

# Hyphenated line-break (word split across lines): "käsit-\ntely" → "käsittely"
_HYPHEN_BREAK_RE = re.compile(r"(\w)-\n(\w)")


def _remove_page_numbers(text: str) -> str:
    return _PAGE_NUMBER_RE.sub("", text)


def _fix_hyphenation(text: str) -> str:
    return _HYPHEN_BREAK_RE.sub(r"\1\2", text)


def _normalize_whitespace(text: str) -> str:
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    # Strip trailing spaces on each line
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


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
