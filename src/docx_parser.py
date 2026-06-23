"""DOCX parsing module for AudiobookMaker.

Extracts text from ``.docx`` (Office Open XML / WordprocessingML) files,
cleans it up, and detects chapters. Designed as a drop-in sibling of
``src.pdf_parser`` and ``src.epub_parser`` — the returned
:class:`~src.pdf_parser.ParsedBook` is the exact same dataclass so
downstream code (chunking, TTS) does not care which format the book
came from.

Why the standard library instead of ``python-docx``:
    A ``.docx`` is just a ZIP archive of XML parts. The two parts we
    need — the document body (``word/document.xml``) and the core
    properties (``docProps/core.xml``) — are plain WordprocessingML that
    ``zipfile`` + ``xml.etree`` read directly. Parsing them ourselves
    keeps this module dependency-free, which matters here for three
    concrete reasons:

      1. The Chatterbox engine runs in a *separate* venv and imports
         this module directly (see
         ``scripts/generate_chatterbox_audiobook.py``); a third-party
         dependency would have to be installed there too, not just in
         the GUI venv.
      2. The frozen ``.exe`` would otherwise need an extra PyInstaller
         ``hiddenimport`` and ship more bytes.
      3. House style keeps the bundle lean (see the release-bundle
         audit policy in ``docs/``).

    The trade-off is that we handle a deliberately small slice of OOXML
    — paragraphs and their heading styles — which covers ordinary book
    manuscripts. Exotic constructs (text boxes, SmartArt) are ignored,
    the same way the PDF and EPUB parsers ignore non-text content.

Strategy:
    1. Open the archive; read ``word/document.xml``.
    2. Walk paragraphs (``<w:p>``) in document order; a paragraph's text
       is the concatenation of its runs (``<w:t>``), with tabs, line
       breaks, and non-breaking hyphens preserved. Tracked deletions
       (``<w:del>``) are dropped so only accepted text is read.
    3. A paragraph whose style is a heading (``Heading1`` … ``Heading3``
       or ``Title``) starts a new chapter; everything else is body text
       appended to the current chapter.
    4. If the document carries no heading styles at all, fall back to
       ``pdf_parser._split_into_chapters`` so the same regex heading
       heuristics (``Luku 3``, ALL-CAPS lines, …) and single-chapter
       fallback used for PDFs apply here too.
    5. Re-use ``pdf_parser.clean_text`` so paragraph / whitespace rules
       stay consistent across formats.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from src.pdf_parser import (
    BookMetadata,
    Chapter,
    EmptyPDFError,
    ParsedBook,
    _split_into_chapters,
    clean_text,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OOXML constants
# ---------------------------------------------------------------------------

# WordprocessingML + package namespaces. A ``.docx`` always uses these fixed
# URIs regardless of the authoring application, so hardcoding them is safe.
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DC = "{http://purl.org/dc/elements/1.1/}"

# ZIP members we read. Both are mandated by the OOXML package format; the
# core-properties part is technically optional, so we tolerate its absence.
_DOCUMENT_PART = "word/document.xml"
_CORE_PROPS_PART = "docProps/core.xml"

# Paragraph style ids that mark a chapter boundary. Matched case-insensitively
# against the style id with spaces removed, so ``Heading1`` and ``heading 1``
# both hit. We stop at level 3 (plus ``Title``) on purpose: deeper headings
# (``Heading4`` …) are treated as ordinary body text so a document with many
# sub-sub-sections does not shatter into hundreds of tiny tracks.
_HEADING_STYLE_IDS = {"heading1", "heading2", "heading3", "title"}

# First-chapter title for content that appears before the first heading
# (title page, preface). Mirrors ``pdf_parser._split_into_chapters`` so the
# pre-heading chapter is named identically across formats.
_DEFAULT_FIRST_TITLE = "Alkusanat"


class EmptyDOCXError(EmptyPDFError):
    """Raised when a DOCX contains no extractable text."""

    pass


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def _read_part(zf: zipfile.ZipFile, name: str) -> bytes | None:
    """Return the bytes of ZIP member ``name``, or ``None`` if it is absent."""
    try:
        return zf.read(name)
    except KeyError:
        return None


def _paragraph_text(paragraph: ET.Element) -> str:
    """Return the visible text of a ``<w:p>`` paragraph.

    Walks the paragraph in document order and stitches the pieces
    together: text runs (``<w:t>``) contribute their text, tabs
    (``<w:tab>``) become a literal tab, line/page breaks (``<w:br>`` /
    ``<w:cr>``) become a newline, and non-breaking hyphens
    (``<w:noBreakHyphen>``) become a literal ``-`` — they are visible
    content, unlike soft hyphens. ``clean_text`` later normalizes the
    whitespace, so the goal here is only to avoid words running together
    across a tab or break.

    Tracked deletions (``<w:del>``) are pruned entirely, so only the text
    a reader would hear with changes accepted is extracted; insertions
    (``<w:ins>``) carry ordinary ``<w:t>`` runs and are kept. The walk
    recurses by hand rather than via ``iter()`` precisely so a ``<w:del>``
    subtree can be skipped — ``iter()`` would still yield its descendants.
    """
    parts: list[str] = []

    def collect(element: ET.Element) -> None:
        for node in element:
            tag = node.tag
            if tag == _W + "del":
                continue  # tracked deletion — prune the whole subtree
            if tag == _W + "t":
                parts.append(node.text or "")
            elif tag == _W + "tab":
                parts.append("\t")
            elif tag in (_W + "br", _W + "cr"):
                parts.append("\n")
            elif tag == _W + "noBreakHyphen":
                parts.append("-")
            collect(node)

    collect(paragraph)
    return "".join(parts)


def _is_heading_paragraph(paragraph: ET.Element) -> bool:
    """True if the paragraph's style id marks it as a heading."""
    p_pr = paragraph.find(_W + "pPr")
    if p_pr is None:
        return False
    p_style = p_pr.find(_W + "pStyle")
    if p_style is None:
        return False
    style_id = p_style.get(_W + "val") or ""
    return style_id.lower().replace(" ", "") in _HEADING_STYLE_IDS


def _extract_paragraphs(document_root: ET.Element) -> list[tuple[str, bool]]:
    """Return ``(text, is_heading)`` for every paragraph in body order.

    ``body.iter`` yields paragraphs nested inside tables too (a table cell
    is built from ``<w:p>`` elements), so table prose is captured rather
    than silently dropped — the same way PyMuPDF returns table text in the
    PDF path.
    """
    body = document_root.find(_W + "body")
    if body is None:
        return []
    paragraphs: list[tuple[str, bool]] = []
    for paragraph in body.iter(_W + "p"):
        paragraphs.append(
            (_paragraph_text(paragraph), _is_heading_paragraph(paragraph))
        )
    return paragraphs


def _build_chapters(paragraphs: list[tuple[str, bool]]) -> list[Chapter]:
    """Turn the flat paragraph list into chapters.

    When the document uses heading styles, each heading starts a new
    chapter and its text becomes the chapter title. Otherwise the whole
    body is handed to ``pdf_parser._split_into_chapters`` so the regex
    heading heuristics and single-chapter fallback shared with the PDF
    path apply.
    """
    has_style_headings = any(is_heading for _, is_heading in paragraphs)
    if not has_style_headings:
        full_text = "\n".join(text for text, _ in paragraphs)
        return _split_into_chapters([(1, full_text)])

    chapters: list[Chapter] = []
    current_title = _DEFAULT_FIRST_TITLE
    current_body: list[str] = []
    chapter_index = 0

    def flush() -> None:
        nonlocal chapter_index
        content = clean_text("\n\n".join(current_body))
        if content.strip():
            chapters.append(
                Chapter(
                    title=current_title,
                    content=content,
                    # DOCX has no fixed pages; keep the fields populated so
                    # the Chapter dataclass stays compatible with the PDF
                    # code path (page numbers are reused as a 1-based index).
                    page_start=chapter_index + 1,
                    page_end=chapter_index + 1,
                    index=chapter_index,
                )
            )
            chapter_index += 1

    for text, is_heading in paragraphs:
        if is_heading and text.strip():
            flush()
            current_title = text.strip()
            current_body = []
        elif text.strip():
            current_body.append(text)
    flush()

    # A document that is all headings and no body would yield nothing; fall
    # back to the heuristic splitter so we never return zero chapters when
    # there is text to read.
    if not chapters:
        full_text = "\n".join(text for text, _ in paragraphs)
        return _split_into_chapters([(1, full_text)])
    return chapters


def _extract_metadata(
    core_props_xml: bytes | None, file_path: str, num_chapters: int
) -> BookMetadata:
    """Pull title / author / subject from ``docProps/core.xml``.

    Core properties are Dublin Core elements (``dc:title``,
    ``dc:creator``, ``dc:subject``). Any missing or unparseable part
    degrades to empty strings; the title additionally falls back to the
    file-name stem so the progress UI always has something to show.
    """
    title = author = subject = ""
    if core_props_xml:
        try:
            root = ET.fromstring(core_props_xml)
        except ET.ParseError as exc:
            logger.warning("DOCX core properties unparseable: %s", exc)
            root = None
        if root is not None:
            title = _first_text(root, _DC + "title")
            author = _first_text(root, _DC + "creator")
            subject = _first_text(root, _DC + "subject")

    if not title:
        # Mirror pdf_parser's filename-to-title rule (underscores AND hyphens
        # become spaces) so the same file stem yields the same title in any
        # format.
        title = Path(file_path).stem.replace("_", " ").replace("-", " ").title()

    return BookMetadata(
        title=title,
        author=author,
        subject=subject,
        # ``num_pages`` is semantically wrong for DOCX (no fixed pages),
        # but the field is reused as a chapter count so the progress UI has
        # something to show. Name stays for drop-in compatibility.
        num_pages=num_chapters,
        file_path=file_path,
    )


def _first_text(root: ET.Element, tag: str) -> str:
    node = root.find(tag)
    if node is None or node.text is None:
        return ""
    return node.text.strip()


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def parse_docx(file_path: str | Path) -> ParsedBook:
    """Parse a DOCX file into a :class:`ParsedBook`.

    Mirrors :func:`src.pdf_parser.parse_pdf` and
    :func:`src.epub_parser.parse_epub` so any parser can feed the same
    TTS pipeline.

    Args:
        file_path: Path to the ``.docx`` archive.

    Raises:
        FileNotFoundError: The file does not exist.
        ValueError: The archive is unreadable as a DOCX (not a ZIP, or
            missing the ``word/document.xml`` part).
        EmptyDOCXError: The DOCX has no extractable text.
    """
    file_path = str(file_path)
    if not Path(file_path).exists():
        raise FileNotFoundError(f"DOCX not found: {file_path}")

    try:
        with zipfile.ZipFile(file_path) as zf:
            document_xml = _read_part(zf, _DOCUMENT_PART)
            core_props_xml = _read_part(zf, _CORE_PROPS_PART)
    except (zipfile.BadZipFile, OSError) as exc:
        # BadZipFile: not a ZIP. OSError (PermissionError, IsADirectoryError,
        # …): the path exists but cannot be opened. Both map to the same
        # "unreadable archive" contract the sibling parsers expose.
        raise ValueError(f"Cannot open DOCX: {file_path}") from exc

    if document_xml is None:
        # A valid ZIP, but not a Word document (no main part).
        raise ValueError(
            f"Cannot open DOCX (missing {_DOCUMENT_PART}): {file_path}"
        )

    try:
        document_root = ET.fromstring(document_xml)
    except ET.ParseError as exc:
        raise ValueError(f"Cannot parse DOCX document body: {file_path}") from exc

    paragraphs = _extract_paragraphs(document_root)
    chapters = _build_chapters(paragraphs)
    metadata = _extract_metadata(core_props_xml, file_path, len(chapters))

    parsed = ParsedBook(metadata=metadata, chapters=chapters)
    if parsed.total_chars == 0:
        raise EmptyDOCXError(
            f"DOCX contains no extractable text ({len(paragraphs)} paragraphs). "
            "The file may be image-only — try a different source."
        )
    return parsed
