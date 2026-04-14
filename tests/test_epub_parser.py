"""Tests for src.epub_parser — the EPUB input path for AudiobookMaker."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ebooklib import epub

from src.epub_parser import (
    EmptyEPUBError,
    _MIN_ITEM_CHARS,
    parse_epub,
)
from src.pdf_parser import Chapter, ParsedBook


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

# Repo root holds the Rubicon test file. Resolve it once so every test that
# touches the real file can reuse it without recomputing the path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_RUBICON = (
    _REPO_ROOT
    / "Rubicon_The_Last_Years_of_the_Roman_Republic_Holland,_Tom_2003_Anchor.epub"
)


def _make_epub(
    items: list[tuple[str, str, str]],
    title: str = "Test Book",
    author: str = "Test Author",
) -> str:
    """Write a tiny EPUB to a temp file. Returns the path.

    ``items`` is a list of ``(file_name, heading, body_html)`` triples.
    Keeping this helper in-test lets us spin up hand-crafted books that
    exercise edge cases (empty items, titles in different tags) without
    bundling more binary fixtures.
    """
    book = epub.EpubBook()
    book.set_identifier("id-test")
    book.set_title(title)
    book.set_language("en")
    book.add_author(author)

    epub_items = []
    for idx, (fn, heading, body) in enumerate(items):
        c = epub.EpubHtml(
            title=heading or f"Chapter {idx}",
            file_name=fn,
            lang="en",
        )
        html = "<html><body>"
        if heading:
            html += f"<h1>{heading}</h1>"
        html += body
        html += "</body></html>"
        c.content = html
        book.add_item(c)
        epub_items.append(c)

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = tuple(epub_items)
    book.spine = ["nav", *epub_items]

    tmp = tempfile.NamedTemporaryFile(suffix=".epub", delete=False)
    tmp.close()
    epub.write_epub(tmp.name, book)
    return tmp.name


# ---------------------------------------------------------------------------
# Hand-crafted EPUB tests
# ---------------------------------------------------------------------------


class TestParseEpubBasic:
    def test_valid_epub_parses_to_multiple_chapters(self) -> None:
        path = _make_epub(
            [
                ("c1.xhtml", "First Chapter", "<p>" + "Hello world. " * 40 + "</p>"),
                ("c2.xhtml", "Second Chapter", "<p>" + "Another page. " * 40 + "</p>"),
                ("c3.xhtml", "Third Chapter", "<p>" + "Third body. " * 40 + "</p>"),
            ]
        )
        book = parse_epub(path)
        assert isinstance(book, ParsedBook)
        # Drop the EpubNav item which ebooklib appends automatically; we
        # only care that real chapters came through.
        real_chapters = [c for c in book.chapters if c.title not in ("", "Chapter")]
        assert len(real_chapters) >= 3

    def test_chapter_titles_extracted_from_h1(self) -> None:
        path = _make_epub(
            [
                ("c1.xhtml", "Preface", "<p>" + "Preface body. " * 40 + "</p>"),
                ("c2.xhtml", "Chapter One", "<p>" + "First. " * 50 + "</p>"),
            ]
        )
        book = parse_epub(path)
        titles = [c.title for c in book.chapters]
        assert "Preface" in titles
        assert "Chapter One" in titles

    def test_empty_items_are_filtered(self) -> None:
        # The first two items are well under _MIN_ITEM_CHARS and must not
        # produce chapters. The third is long enough and must survive.
        # ebooklib.write_epub fails if body content is empty, so a
        # minimal non-empty blank-page body is used for the "empty" item.
        path = _make_epub(
            [
                ("blank.xhtml", "", "<p>&nbsp;</p>"),
                ("tiny.xhtml", "Short", "<p>tiny</p>"),
                ("real.xhtml", "Real", "<p>" + "Real content. " * 50 + "</p>"),
            ]
        )
        book = parse_epub(path)
        contents = [c.content for c in book.chapters]
        # No chapter should contain the "tiny" stub — it was under the
        # MIN_ITEM_CHARS threshold and should have been dropped.
        assert all("tiny" not in c for c in contents)
        assert any("Real content." in c for c in contents)

    def test_returns_chapters_as_pdf_parser_compatible_objects(self) -> None:
        path = _make_epub(
            [("c1.xhtml", "A Chapter", "<p>" + "A body. " * 40 + "</p>")]
        )
        book = parse_epub(path)
        assert all(isinstance(c, Chapter) for c in book.chapters)
        # full_text and total_chars are the properties the TTS pipeline reads.
        assert book.full_text.strip() != ""
        assert book.total_chars > 0

    def test_bad_path_raises_filenotfound(self) -> None:
        with pytest.raises(FileNotFoundError):
            parse_epub("D:/definitely/does/not/exist.epub")

    def test_unreadable_file_raises_valueerror(self, tmp_path: Path) -> None:
        bogus = tmp_path / "not_an_epub.epub"
        bogus.write_text("this is not a zip archive", encoding="utf-8")
        with pytest.raises(ValueError):
            parse_epub(str(bogus))


# ---------------------------------------------------------------------------
# Full round-trip with the bundled Rubicon file
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _RUBICON.exists(),
    reason=f"Rubicon test EPUB not present at {_RUBICON}",
)
class TestParseEpubRubicon:
    """Sanity checks against a real ~800k char English history EPUB."""

    def test_rubicon_has_many_chapters(self) -> None:
        book = parse_epub(str(_RUBICON))
        # Rubicon has 28 document items; several are front-matter stubs
        # under _MIN_ITEM_CHARS. Even after filtering we expect well over
        # five real chapters.
        assert len(book.chapters) > 5

    def test_rubicon_has_substantial_char_count(self) -> None:
        book = parse_epub(str(_RUBICON))
        assert book.total_chars > 100_000

    def test_rubicon_metadata_extracted(self) -> None:
        book = parse_epub(str(_RUBICON))
        assert "Rubicon" in book.metadata.title
        assert "Holland" in book.metadata.author

    def test_rubicon_no_replacement_chars_leak_into_content(self) -> None:
        # Our parser strips the U+FFFD replacement character so it never
        # reaches the TTS step (where it would be read as "question mark").
        book = parse_epub(str(_RUBICON))
        for ch in book.chapters:
            assert "\ufffd" not in ch.content, (
                f"Replacement char leaked into chapter {ch.index} {ch.title!r}"
            )
