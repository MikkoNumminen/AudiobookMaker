"""Tests for src.epub_parser — the EPUB input path for AudiobookMaker."""

from __future__ import annotations

import logging
import os
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

# Optional real-EPUB round-trip suite. Set AUDIOBOOKMAKER_TEST_REAL_EPUB to
# the absolute path of a substantial EPUB (e.g. one under .local/sources/)
# to opt in. The path itself is never committed — keep test inputs out of
# the public repo per CLAUDE.md's no-third-party-material policy.
_REAL_EPUB_PATH = os.environ.get("AUDIOBOOKMAKER_TEST_REAL_EPUB", "")
_REAL_EPUB = Path(_REAL_EPUB_PATH) if _REAL_EPUB_PATH else None


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

    def test_replacement_chars_stripped_from_content(self) -> None:
        """U+FFFD must never reach the TTS step.

        The TTS pipeline reads chapter.content verbatim. If a malformed
        EPUB carries the replacement character (U+FFFD), the engine would
        synthesize a literal "question mark" sound between sentences.
        ``epub_parser`` strips it during content normalization.

        This is a hand-crafted regression test so the contract is
        enforced in CI; the opt-in real-EPUB suite also exercises the
        same path on real-world content when a tester sets the env var.
        """
        path = _make_epub(
            [
                (
                    "c1.xhtml",
                    "Replacement Chapter",
                    "<p>"
                    + ("Hello�world. The fog � over the harbour. " * 8)
                    + "</p>",
                ),
            ]
        )
        book = parse_epub(path)
        # At least one chapter must have survived the parser; this rules
        # out the degenerate "all chapters dropped" failure mode.
        assert book.chapters, "parser dropped every chapter"
        for ch in book.chapters:
            assert "�" not in ch.content, (
                f"Replacement char leaked into chapter {ch.index} {ch.title!r}"
            )


# ---------------------------------------------------------------------------
# Spine-iteration error logging
# ---------------------------------------------------------------------------


class _ExplodingSpine:
    """Iterable whose second yield raises — mimics a malformed spine."""

    def __iter__(self):
        yield ("valid_id",)
        raise RuntimeError("spine corruption")


class _SpineBook:
    """Minimal stand-in for the object returned by ``epub.read_epub``."""

    def __init__(self, spine) -> None:
        self.spine = spine

    def get_item_with_id(self, _item_id):  # noqa: D401 - simple override
        return None

    def get_items(self):
        return []


def test_spine_iteration_failure_logged(monkeypatch, tmp_path, caplog) -> None:
    """A malformed spine (one that raises mid-iteration) must leave a
    warning breadcrumb instead of silently dropping the rest of the
    chapter list."""
    from src import epub_parser

    fake_path = tmp_path / "fake.epub"
    fake_path.write_bytes(b"fake")

    monkeypatch.setattr(
        epub_parser.epub,
        "read_epub",
        lambda _p: _SpineBook(_ExplodingSpine()),
    )

    with caplog.at_level(logging.WARNING, logger="src.epub_parser"):
        with pytest.raises(EmptyEPUBError):
            parse_epub(str(fake_path))

    assert any(
        "EPUB spine iteration failed" in record.getMessage()
        and "spine corruption" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Full round-trip with a real EPUB (opt-in via AUDIOBOOKMAKER_TEST_REAL_EPUB)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _REAL_EPUB is None or not _REAL_EPUB.exists(),
    reason=(
        "AUDIOBOOKMAKER_TEST_REAL_EPUB is not set or does not point at an "
        "existing file. Set it to an absolute path to opt into the "
        "real-EPUB round-trip suite."
    ),
)
class TestParseEpubRealBook:
    """Sanity checks against a real, substantial EPUB.

    Hand-crafted fixtures in TestParseEpubBasic cover correctness; this
    suite verifies that the parser behaves on a real-world file (encoding
    edge cases, large content, full metadata round-trip). Opt in by
    pointing AUDIOBOOKMAKER_TEST_REAL_EPUB at a local EPUB; skipped
    otherwise.
    """

    def test_real_epub_has_many_chapters(self) -> None:
        book = parse_epub(str(_REAL_EPUB))
        # A real book always has more than a handful of chapter-sized
        # items after front-matter filtering. Five is a low bar that any
        # full-length work will clear.
        assert len(book.chapters) > 5

    def test_real_epub_has_substantial_char_count(self) -> None:
        book = parse_epub(str(_REAL_EPUB))
        # A real book is at least 100k characters. This guards against a
        # regression where the parser silently drops most of the spine.
        assert book.total_chars > 100_000

    def test_real_epub_metadata_extracted(self) -> None:
        book = parse_epub(str(_REAL_EPUB))
        # We don't pin specific strings (the file could be anything); we
        # only assert that title and author came through non-empty, which
        # is the metadata contract.
        assert book.metadata.title.strip() != ""
        assert book.metadata.author.strip() != ""

    def test_real_epub_no_replacement_chars_leak_into_content(self) -> None:
        # Our parser strips the U+FFFD replacement character so it never
        # reaches the TTS step (where it would be read as "question mark").
        book = parse_epub(str(_REAL_EPUB))
        for ch in book.chapters:
            assert "\ufffd" not in ch.content, (
                f"Replacement char leaked into chapter {ch.index} {ch.title!r}"
            )
