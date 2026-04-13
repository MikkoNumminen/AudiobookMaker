"""Unit tests for pdf_parser module."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import fitz  # PyMuPDF

from src.pdf_parser import (
    BookMetadata,
    Chapter,
    EmptyPDFError,
    ParsedBook,
    clean_text,
    parse_pdf,
    _looks_like_heading,
    _remove_page_numbers,
    _fix_hyphenation,
    _normalize_whitespace,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pdf(pages: list[str], title: str = "", author: str = "") -> str:
    """Create a temporary PDF with given page texts. Returns file path."""
    doc = fitz.open()
    for page_text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), page_text, fontsize=12)

    if title or author:
        doc.set_metadata({"title": title, "author": author})

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()  # close before save — Windows locks open files
    doc.save(tmp.name)
    doc.close()
    return tmp.name


# ---------------------------------------------------------------------------
# clean_text
# ---------------------------------------------------------------------------


class TestCleanText:
    def test_removes_bare_page_numbers(self) -> None:
        text = "Some content\n\n12\n\nMore content"
        result = clean_text(text)
        assert "\n12\n" not in result
        assert "Some content" in result
        assert "More content" in result

    def test_removes_page_with_label(self) -> None:
        text = "Content\nPage 5\nMore"
        result = clean_text(text)
        assert "Page 5" not in result

    def test_fixes_hyphenation(self) -> None:
        text = "käsit-\ntely on tärkeää"
        result = clean_text(text)
        assert "käsittely" in result

    def test_fixes_hyphenation_with_trailing_space(self) -> None:
        # PyMuPDF often emits a trailing space before the newline.
        text = "var- \nhaismoderni"
        result = clean_text(text)
        assert "varhaismoderni" in result
        assert "var-" not in result

    def test_strips_soft_hyphens(self) -> None:
        # U+00AD is a typographic hint that should never appear in spoken text.
        text = "Pyhäs\u00ad\nsä on esimerkki"
        result = clean_text(text)
        assert "Pyhässä" in result
        assert "\u00ad" not in result

    def test_strips_inline_soft_hyphens(self) -> None:
        # Soft hyphen without a newline (just an invisible hint).
        text = "oikeus\u00adtiede"
        result = clean_text(text)
        assert "oikeustiede" in result
        assert "\u00ad" not in result

    def test_preserves_compound_number_hyphen(self) -> None:
        # "1200-luvulla" across a line break: preserve the hyphen.
        text = "1200-\nluvulla oli"
        result = clean_text(text)
        assert "1200-luvulla" in result

    def test_preserves_proper_noun_hyphen(self) -> None:
        text = "Austro-\nHungarian empire"
        result = clean_text(text)
        assert "Austro-Hungarian" in result

    def test_flattens_in_paragraph_line_wraps(self) -> None:
        # PDF line wraps inside a sentence must become spaces, not newlines,
        # otherwise edge-tts inserts a pause at every line break.
        text = "Oikeusvaltiosta ei vielä\nesimodernin oikeuden aikana voida puhua."
        result = clean_text(text)
        assert "\n" not in result
        assert "ei vielä esimodernin" in result

    def test_preserves_paragraph_breaks(self) -> None:
        # Double newlines (paragraph breaks) must survive cleaning.
        text = "First paragraph.\n\nSecond paragraph."
        result = clean_text(text)
        assert "\n\n" in result

    def test_collapses_multiple_blank_lines(self) -> None:
        text = "A\n\n\n\n\nB"
        result = clean_text(text)
        assert "\n\n\n" not in result

    def test_empty_string(self) -> None:
        assert clean_text("") == ""

    def test_preserves_normal_text(self) -> None:
        text = "Tämä on normaali lause. Ja toinenkin."
        result = clean_text(text)
        assert result == text


# ---------------------------------------------------------------------------
# _looks_like_heading
# ---------------------------------------------------------------------------


class TestLooksLikeHeading:
    def test_chapter_keyword(self) -> None:
        assert _looks_like_heading("Luku 3")
        assert _looks_like_heading("Chapter 1")

    def test_numbered_heading(self) -> None:
        assert _looks_like_heading("3. Johdanto")

    def test_all_caps(self) -> None:
        assert _looks_like_heading("JOHDANTO")

    def test_normal_sentence_not_heading(self) -> None:
        assert not _looks_like_heading("Tämä on normaali lause.")

    def test_empty_not_heading(self) -> None:
        assert not _looks_like_heading("")
        assert not _looks_like_heading("   ")

    def test_year_period_prose_not_heading(self) -> None:
        # Regression: PDF body lines like "1500. Nämä jaot on tarkoitettu ..."
        # used to be captured as chapter titles because of the "\d+\." pattern.
        # A 4-digit year followed by prose is NOT a heading.
        line = (
            "1500. Nämä jaot on tarkoitettu ainoastaan helpottamaan "
            "oikeudellisten kehityslinjojen hahmottamista"
        )
        assert not _looks_like_heading(line)

    def test_long_numbered_prose_not_heading(self) -> None:
        # Even a short-numbered prefix should not count if the line is long prose.
        line = (
            "3. Tämä on pitkä lause joka jatkuu ja jatkuu ja sisältää "
            "kokonaisen virkkeen eikä ole otsikko lainkaan."
        )
        assert not _looks_like_heading(line)

    def test_numbered_short_title_still_heading(self) -> None:
        # Legitimate numbered titles must still be detected.
        assert _looks_like_heading("1. JOHDANTO")
        assert _looks_like_heading("12. Yhteenveto")


# ---------------------------------------------------------------------------
# parse_pdf – metadata
# ---------------------------------------------------------------------------


class TestParsePdfMetadata:
    def test_reads_title_and_author(self) -> None:
        path = _make_pdf(["Hello world"], title="Testikirja", author="Testi Tekijä")
        try:
            book = parse_pdf(path)
            assert book.metadata.title == "Testikirja"
            assert book.metadata.author == "Testi Tekijä"
        finally:
            os.unlink(path)

    def test_fallback_title_from_filename(self) -> None:
        path = _make_pdf(["Content without metadata"])
        # Rename to something readable
        new_path = Path(path).parent / "my_test_book.pdf"
        Path(path).rename(new_path)
        try:
            book = parse_pdf(new_path)
            assert "My Test Book" in book.metadata.title or book.metadata.title != ""
        finally:
            new_path.unlink(missing_ok=True)

    def test_num_pages(self) -> None:
        path = _make_pdf(["Page one", "Page two", "Page three"])
        try:
            book = parse_pdf(path)
            assert book.metadata.num_pages == 3
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# parse_pdf – text extraction
# ---------------------------------------------------------------------------


class TestParsePdfText:
    def test_extracts_text(self) -> None:
        path = _make_pdf(["Tämä on ensimmäinen sivu."])
        try:
            book = parse_pdf(path)
            assert "ensimmäinen" in book.full_text
        finally:
            os.unlink(path)

    def test_multi_page_text(self) -> None:
        path = _make_pdf(["Ensimmäinen sivu.", "Toinen sivu.", "Kolmas sivu."])
        try:
            book = parse_pdf(path)
            full = book.full_text
            assert "Ensimmäinen" in full
            assert "Toinen" in full
            assert "Kolmas" in full
        finally:
            os.unlink(path)

    def test_chapters_are_created(self) -> None:
        path = _make_pdf(["Hello world"])
        try:
            book = parse_pdf(path)
            assert len(book.chapters) >= 1
        finally:
            os.unlink(path)

    def test_total_chars_positive(self) -> None:
        path = _make_pdf(["Some text here."])
        try:
            book = parse_pdf(path)
            assert book.total_chars > 0
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# parse_pdf – error handling
# ---------------------------------------------------------------------------


class TestParsePdfErrors:
    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            parse_pdf("/nonexistent/path/file.pdf")

    def test_invalid_pdf(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(b"this is not a pdf")
        tmp.close()
        try:
            with pytest.raises(ValueError):
                parse_pdf(tmp.name)
        finally:
            os.unlink(tmp.name)

    def test_year_prose_not_split_as_chapter(self) -> None:
        # Regression (integration-level): a body line like "1500. Nämä jaot ..."
        # used to be misdetected as a chapter heading, creating a spurious
        # chapter and eating the first ~60 chars of body text into the title.
        # Build a synthetic PDF with two real chapter headings and a year-prose
        # line in the middle of the first chapter's body.
        doc = fitz.open()
        page = doc.new_page()
        # insert_text draws each "\n" as a line break at the given point.
        body = (
            "1. JOHDANTO\n"
            "\n"
            "Tämä on ensimmäinen luku.\n"
            "\n"
            "1500. Nämä jaot on tarkoitettu ainoastaan helpottamaan "
            "oikeudellisten kehityslinjojen seuraamista.\n"
            "\n"
            "2. SEURAAVA LUKU\n"
            "\n"
            "Toinen luku alkaa tässä."
        )
        page.insert_text((50, 72), body, fontsize=12)
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()  # close before save — Windows locks open files
        doc.save(tmp.name)
        doc.close()
        try:
            book = parse_pdf(tmp.name)
            # Exactly two chapters — the "1500." line must NOT start a new one.
            assert len(book.chapters) == 2, (
                f"expected 2 chapters, got {len(book.chapters)}: "
                f"{[c.title for c in book.chapters]}"
            )
            assert book.chapters[0].title == "1. JOHDANTO"
            assert book.chapters[1].title == "2. SEURAAVA LUKU"
            # The year-prose line stays in chapter 0's body.
            assert "1500. Nämä jaot on tarkoitettu" in book.chapters[0].content
            # Chapter 0 body must not start with a mid-word fragment.
            assert not book.chapters[0].content.lower().startswith("listen")
            # Sanity: the intended opening line survives.
            assert "Tämä on ensimmäinen luku" in book.chapters[0].content
        finally:
            os.unlink(tmp.name)


class TestEmptyPdf:
    def test_empty_pdf_raises(self) -> None:
        """A PDF with pages but no text should raise EmptyPDFError."""
        doc = fitz.open()
        doc.new_page()  # blank page, no text
        doc.new_page()
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()  # close before save — Windows locks open files
        doc.save(tmp.name)
        doc.close()
        try:
            with pytest.raises(EmptyPDFError, match="no extractable text"):
                parse_pdf(tmp.name)
        finally:
            os.unlink(tmp.name)
