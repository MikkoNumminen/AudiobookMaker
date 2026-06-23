"""Tests for src.docx_parser — the DOCX input path for AudiobookMaker.

Fixtures are built synthetically with the standard library (``zipfile``
plus hand-written WordprocessingML), never from real Word documents, per
the no-third-party-material policy in CLAUDE.md. Building the OOXML by
hand also means these tests exercise the parser against real ``.docx``
bytes rather than a library round-trip.
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import pytest

from src.docx_parser import EmptyDOCXError, parse_docx
from src.pdf_parser import Chapter, ParsedBook

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _paragraph_xml(text: str, style: str | None = None) -> str:
    """Render a single ``<w:p>`` paragraph, optionally carrying a style id."""
    inner = ""
    if style is not None:
        inner += f"<w:pPr><w:pStyle w:val={quoteattr(style)}/></w:pPr>"
    inner += f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
    return f"<w:p>{inner}</w:p>"


def _write_docx(
    body_inner_xml: str,
    *,
    title: str | None = "Test Book",
    author: str | None = "Test Author",
    include_core_props: bool = True,
) -> str:
    """Write a minimal-but-valid ``.docx`` to a temp file. Returns the path.

    ``body_inner_xml`` is the raw content of ``<w:body>`` so tests can
    inject tables, tabs, and breaks the structured helper does not model.
    """
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}"><w:body>{body_inner_xml}</w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Types xmlns="{_CT_NS}">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument'
        '.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_REL_NS}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )

    tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document)
        if include_core_props:
            core_bits = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                f'<cp:coreProperties xmlns:cp="{_CP_NS}" xmlns:dc="{_DC_NS}">',
            ]
            if title is not None:
                core_bits.append(f"<dc:title>{escape(title)}</dc:title>")
            if author is not None:
                core_bits.append(f"<dc:creator>{escape(author)}</dc:creator>")
            core_bits.append("</cp:coreProperties>")
            zf.writestr("docProps/core.xml", "".join(core_bits))
    return tmp.name


def _make_docx(
    paragraphs: list[tuple[str, str | None]],
    title: str | None = "Test Book",
    author: str | None = "Test Author",
) -> str:
    """Write a ``.docx`` from ``(text, style|None)`` paragraph tuples."""
    body = "".join(_paragraph_xml(text, style) for text, style in paragraphs)
    return _write_docx(body, title=title, author=author)


# ---------------------------------------------------------------------------
# Heading-style chapter detection
# ---------------------------------------------------------------------------


class TestHeadingStyleChapters:
    def test_heading_styles_split_into_chapters(self) -> None:
        path = _make_docx(
            [
                ("Chapter One", "Heading1"),
                ("First body. " * 20, None),
                ("Chapter Two", "Heading1"),
                ("Second body. " * 20, None),
                ("Chapter Three", "Heading1"),
                ("Third body. " * 20, None),
            ]
        )
        book = parse_docx(path)
        titles = [c.title for c in book.chapters]
        assert titles == ["Chapter One", "Chapter Two", "Chapter Three"]

    def test_heading_id_with_space_is_recognized(self) -> None:
        # Some authoring tools emit the style id as "heading 1" rather than
        # "Heading1"; both must split.
        path = _make_docx(
            [
                ("Intro", "heading 1"),
                ("Body. " * 20, None),
                ("Outro", "heading 2"),
                ("More. " * 20, None),
            ]
        )
        book = parse_docx(path)
        assert [c.title for c in book.chapters] == ["Intro", "Outro"]

    def test_preface_before_first_heading_becomes_its_own_chapter(self) -> None:
        path = _make_docx(
            [
                ("Some front matter before any heading. " * 5, None),
                ("Chapter One", "Heading1"),
                ("Body. " * 20, None),
            ]
        )
        book = parse_docx(path)
        assert book.chapters[0].title == "Alkusanat"
        assert "front matter" in book.chapters[0].content
        assert book.chapters[1].title == "Chapter One"

    def test_deep_headings_do_not_split(self) -> None:
        # Heading4+ is treated as body text so deeply nested documents do
        # not shatter into hundreds of tracks.
        path = _make_docx(
            [
                ("Real Chapter", "Heading1"),
                ("Subsection", "Heading4"),
                ("Body. " * 20, None),
            ]
        )
        book = parse_docx(path)
        assert [c.title for c in book.chapters] == ["Real Chapter"]
        assert "Subsection" in book.chapters[0].content

    def test_title_style_splits_chapters(self) -> None:
        # Word's Title style is in the heading set, so it starts a chapter
        # just like Heading1-3 (also exercises case-insensitive "title").
        path = _make_docx(
            [
                ("My Book", "Title"),
                ("Introduction text. " * 20, None),
                ("First Chapter", "title"),
                ("Chapter content. " * 20, None),
            ]
        )
        book = parse_docx(path)
        assert [c.title for c in book.chapters] == ["My Book", "First Chapter"]


# ---------------------------------------------------------------------------
# Fallback chapter detection (no heading styles)
# ---------------------------------------------------------------------------


class TestFallbackChapters:
    def test_no_headings_yields_single_chapter(self) -> None:
        path = _make_docx(
            [
                ("Just one long flowing paragraph. " * 30, None),
                ("Another plain paragraph. " * 30, None),
            ]
        )
        book = parse_docx(path)
        assert len(book.chapters) == 1
        assert "flowing paragraph" in book.chapters[0].content

    def test_all_caps_lines_split_via_regex_fallback(self) -> None:
        # No heading styles, but ALL-CAPS short lines look like headings to
        # the shared pdf_parser splitter.
        path = _make_docx(
            [
                ("INTRODUCTION", None),
                ("The opening words. " * 20, None),
                ("CONCLUSION", None),
                ("The closing words. " * 20, None),
            ]
        )
        book = parse_docx(path)
        titles = [c.title for c in book.chapters]
        assert "INTRODUCTION" in titles
        assert "CONCLUSION" in titles

    def test_only_headings_no_body_still_yields_chapters(self) -> None:
        # Every paragraph is a heading and there is no body text. The
        # style-based pass produces nothing, so the parser must fall back to
        # the heuristic splitter rather than raise EmptyDOCXError.
        path = _make_docx(
            [
                ("Chapter One", "Heading1"),
                ("Chapter Two", "Heading1"),
            ]
        )
        book = parse_docx(path)
        assert len(book.chapters) >= 1
        assert "Chapter One" in book.full_text
        assert "Chapter Two" in book.full_text


# ---------------------------------------------------------------------------
# ParsedBook contract
# ---------------------------------------------------------------------------


class TestParsedBookContract:
    def test_chapters_are_pdf_parser_compatible(self) -> None:
        path = _make_docx(
            [("A Chapter", "Heading1"), ("A body. " * 30, None)]
        )
        book = parse_docx(path)
        assert all(isinstance(c, Chapter) for c in book.chapters)
        # full_text and total_chars are what the TTS pipeline reads.
        assert book.full_text.strip() != ""
        assert book.total_chars > 0

    def test_tabs_and_breaks_keep_words_apart(self) -> None:
        # A run with a tab and a line break between text nodes must not
        # collapse "A", "B", "C" into "ABC".
        body = (
            "<w:p><w:r>"
            "<w:t>A</w:t><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t>"
            "</w:r></w:p>"
            "<w:p><w:r><w:t>" + ("padding words " * 20) + "</w:t></w:r></w:p>"
        )
        path = _write_docx(body)
        book = parse_docx(path)
        content = book.full_text
        assert "A" in content and "B" in content and "C" in content
        assert "AB" not in content  # tab kept A and B apart
        assert "B C" in content  # break kept B and C apart

    def test_table_text_is_extracted(self) -> None:
        # Paragraphs inside a table cell are still <w:p> and must be read.
        body = (
            "<w:tbl><w:tr><w:tc>"
            "<w:p><w:r><w:t>" + ("cell prose " * 20) + "</w:t></w:r></w:p>"
            "</w:tc></w:tr></w:tbl>"
        )
        path = _write_docx(body)
        book = parse_docx(path)
        assert "cell prose" in book.full_text

    def test_non_breaking_hyphen_is_preserved(self) -> None:
        # <w:noBreakHyphen/> is visible content (unlike a soft hyphen) and
        # must survive as a literal "-" so compounds stay intact.
        body = (
            "<w:p><w:r>"
            "<w:t>mother</w:t><w:noBreakHyphen/>"
            "<w:t>in</w:t><w:noBreakHyphen/><w:t>law spoke. </w:t>"
            "</w:r></w:p>"
            "<w:p><w:r><w:t>" + ("padding words " * 20) + "</w:t></w:r></w:p>"
        )
        path = _write_docx(body)
        book = parse_docx(path)
        assert "mother-in-law" in book.full_text


class TestTrackedChanges:
    def test_tracked_deletions_are_dropped(self) -> None:
        # Text inside a <w:del> (a tracked deletion) must not reach the
        # audio — only what a reader would hear with changes accepted.
        # The deleted run here uses a plain <w:t> on purpose: that is the
        # case the manual subtree-skip handles (real Word also wraps the
        # run in <w:delText>, which is excluded either way).
        body = (
            "<w:p>"
            "<w:r><w:t>Keep one. </w:t></w:r>"
            "<w:del><w:r><w:t>drop this entirely. </w:t></w:r></w:del>"
            "<w:r><w:t>Keep two.</w:t></w:r>"
            "</w:p>"
            "<w:p><w:r><w:t>" + ("padding words " * 20) + "</w:t></w:r></w:p>"
        )
        path = _write_docx(body)
        book = parse_docx(path)
        assert "Keep one." in book.full_text
        assert "Keep two." in book.full_text
        assert "drop this" not in book.full_text

    def test_tracked_insertions_are_kept(self) -> None:
        # The reverse of a deletion: a <w:ins> insertion is accepted content.
        body = (
            "<w:p>"
            "<w:r><w:t>Before </w:t></w:r>"
            "<w:ins><w:r><w:t>inserted </w:t></w:r></w:ins>"
            "<w:r><w:t>after.</w:t></w:r>"
            "</w:p>"
            "<w:p><w:r><w:t>" + ("padding words " * 20) + "</w:t></w:r></w:p>"
        )
        path = _write_docx(body)
        book = parse_docx(path)
        assert "Before inserted after." in book.full_text


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_title_and_author_from_core_properties(self) -> None:
        path = _make_docx(
            [("Body. " * 30, None)],
            title="My Great Book",
            author="Jane Writer",
        )
        book = parse_docx(path)
        assert book.metadata.title == "My Great Book"
        assert book.metadata.author == "Jane Writer"

    def test_title_falls_back_to_filename(self) -> None:
        # No core.xml at all → title derives from the file stem.
        body = "<w:p><w:r><w:t>" + ("Body. " * 30) + "</w:t></w:r></w:p>"
        path = _write_docx(body, include_core_props=False)
        book = parse_docx(path)
        assert book.metadata.title  # non-empty
        assert book.metadata.author == ""

    def test_missing_title_element_falls_back_to_filename(self) -> None:
        # core.xml present but with no dc:title.
        path = _make_docx([("Body. " * 30, None)], title=None, author="Someone")
        book = parse_docx(path)
        assert book.metadata.title  # non-empty (from filename)
        assert book.metadata.author == "Someone"

    def test_malformed_core_properties_degrade_gracefully(self, caplog) -> None:
        # Valid body, but docProps/core.xml is not well-formed XML. The
        # ParseError must be caught: parse succeeds, title falls back to the
        # filename, author is empty, and a warning is logged.
        import logging

        body = "<w:p><w:r><w:t>" + ("Body. " * 30) + "</w:t></w:r></w:p>"
        document = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="{_W_NS}"><w:body>{body}</w:body></w:document>'
        )
        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.close()
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("word/document.xml", document)
            # Unclosed <dc:title> — not well-formed XML.
            zf.writestr(
                "docProps/core.xml",
                f'<cp:coreProperties xmlns:cp="{_CP_NS}" xmlns:dc="{_DC_NS}">'
                "<dc:title>Broken",
            )
        with caplog.at_level(logging.WARNING, logger="src.docx_parser"):
            book = parse_docx(tmp.name)
        assert isinstance(book, ParsedBook)
        assert book.metadata.title  # filename fallback, non-empty
        assert "Broken" not in book.metadata.title
        assert book.metadata.author == ""
        assert any(
            "core properties" in r.getMessage().lower() for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrors:
    def test_bad_path_raises_filenotfound(self) -> None:
        with pytest.raises(FileNotFoundError):
            parse_docx("D:/definitely/does/not/exist.docx")

    def test_not_a_zip_raises_valueerror(self, tmp_path: Path) -> None:
        bogus = tmp_path / "not_a_docx.docx"
        bogus.write_text("this is not a zip archive", encoding="utf-8")
        with pytest.raises(ValueError):
            parse_docx(str(bogus))

    def test_zip_without_document_part_raises_valueerror(
        self, tmp_path: Path
    ) -> None:
        # A valid ZIP that is not a Word document (no word/document.xml).
        path = tmp_path / "empty.docx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("hello.txt", "not a word file")
        with pytest.raises(ValueError):
            parse_docx(str(path))

    def test_empty_document_raises_emptydocxerror(self) -> None:
        # Whitespace-only paragraphs carry no readable text.
        path = _make_docx([("   ", None), ("", None)])
        with pytest.raises(EmptyDOCXError):
            parse_docx(path)

    def test_malformed_document_xml_raises_valueerror(self) -> None:
        # Valid ZIP and DOCX layout, but document.xml is not well-formed XML
        # (the </w:body> closes before <w:t>/<w:r>/<w:p>). The ParseError is
        # caught and re-raised as a ValueError carrying the file path.
        path = _write_docx("<w:p><w:r><w:t>Unclosed</w:body>")
        with pytest.raises(ValueError, match="Cannot parse DOCX document body"):
            parse_docx(path)


# ---------------------------------------------------------------------------
# clean_text integration
# ---------------------------------------------------------------------------


class TestCleanTextIntegration:
    def test_internal_runs_of_spaces_are_collapsed(self) -> None:
        path = _make_docx(
            [("Hello      world from      the document. " * 10, None)]
        )
        book = parse_docx(path)
        assert "  " not in book.full_text  # double spaces collapsed
        assert "Hello world" in book.full_text
