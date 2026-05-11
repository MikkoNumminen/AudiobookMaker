"""Tests for the OCR fallback path in src.pdf_parser.

Two scenarios anchor the suite:

1. A text-bearing PDF goes through parse_pdf without ever invoking the
   OCR fallback — i.e. PyMuPDF's first pass returns text, the empty-pages
   counter stays at zero, and ocrmypdf is not called.

2. An image-only PDF (synthesized as a blank page so PyMuPDF returns
   empty text) DOES invoke the fallback. We mock ocrmypdf so the suite
   runs without a real Tesseract install — the mock simulates ocrmypdf
   producing a text-bearing PDF, and we verify parse_pdf re-extracts
   from that result.

Synthetic PDFs only — fixtures are generated on-the-fly via PyMuPDF
inside the test. No third-party PDFs ship with the test suite (CLAUDE.md
hygiene rule: nothing copyrighted in tests/).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest

from src import pdf_parser
from src.pdf_parser import EmptyPDFError, parse_pdf


def _write_text_pdf(path: Path, text: str = "Lorem ipsum " * 40) -> Path:
    """Synthesize a one-page text-bearing PDF at ``path``.

    PyMuPDF's ``page.get_text('text')`` returns the inserted string on
    such a page, so parse_pdf's first pass picks it up and the OCR
    fallback never fires.
    """
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), text, fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def _write_empty_pdf(path: Path) -> Path:
    """Synthesize a one-page PDF with no text layer.

    A blank page (no ``insert_text`` calls). PyMuPDF's
    ``get_text('text')`` returns the empty string for such a page,
    which is the trigger we use to fire the OCR fallback in
    ``parse_pdf``.
    """
    doc = fitz.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()
    return path


class TestOCRTriggering:
    """Anchor tests for the trigger contract: OCR fires only when needed."""

    def test_text_bearing_pdf_does_not_trigger_ocr(self, tmp_path: Path) -> None:
        pdf = _write_text_pdf(tmp_path / "text.pdf")
        with patch.object(pdf_parser, "_run_ocr_fallback") as mock_ocr:
            book = parse_pdf(pdf)
        mock_ocr.assert_not_called()
        assert book.total_chars > 0

    def test_empty_pdf_triggers_ocr_and_reuses_result(self, tmp_path: Path) -> None:
        empty = _write_empty_pdf(tmp_path / "empty.pdf")
        # Simulate ocrmypdf producing a text-bearing PDF at this path.
        ocrd = tmp_path / "ocrd.pdf"
        _write_text_pdf(ocrd, text="Reconstructed by OCR " * 20)

        with patch.object(
            pdf_parser, "_run_ocr_fallback", return_value=ocrd,
        ) as mock_ocr:
            book = parse_pdf(empty)

        mock_ocr.assert_called_once()
        args, _ = mock_ocr.call_args
        assert args[0] == str(empty)
        assert book.total_chars > 0
        assert "Reconstructed by OCR" in book.full_text

    def test_empty_pdf_with_unavailable_ocr_raises_EmptyPDFError(
        self, tmp_path: Path,
    ) -> None:
        empty = _write_empty_pdf(tmp_path / "empty.pdf")
        with patch.object(pdf_parser, "_run_ocr_fallback", return_value=None):
            with pytest.raises(EmptyPDFError):
                parse_pdf(empty)


class TestParsePdfArguments:
    """parse_pdf must forward ocr_language and ocr_cache_dir through."""

    def test_ocr_language_forwarded(self, tmp_path: Path) -> None:
        empty = _write_empty_pdf(tmp_path / "empty.pdf")
        ocrd = tmp_path / "ocrd.pdf"
        _write_text_pdf(ocrd, text="forwarded language " * 10)
        with patch.object(
            pdf_parser, "_run_ocr_fallback", return_value=ocrd,
        ) as mock_ocr:
            parse_pdf(empty, ocr_language="fin")
        args, _ = mock_ocr.call_args
        # Signature: _run_ocr_fallback(source_path, ocr_language, cache_dir)
        assert args[1] == "fin"

    def test_ocr_cache_dir_forwarded(self, tmp_path: Path) -> None:
        empty = _write_empty_pdf(tmp_path / "empty.pdf")
        ocrd = tmp_path / "ocrd.pdf"
        _write_text_pdf(ocrd, text="forwarded cache dir " * 10)
        cache_dir = tmp_path / "cache"
        with patch.object(
            pdf_parser, "_run_ocr_fallback", return_value=ocrd,
        ) as mock_ocr:
            parse_pdf(empty, ocr_cache_dir=cache_dir)
        args, _ = mock_ocr.call_args
        assert args[2] == cache_dir


class TestTesseractLanguageMapping:
    """ocr_path.tesseract_lang_for maps TTS codes to Tesseract codes."""

    def test_fi_maps_to_fin(self) -> None:
        from src.ocr_path import tesseract_lang_for
        assert tesseract_lang_for("fi") == "fin"

    def test_en_maps_to_eng(self) -> None:
        from src.ocr_path import tesseract_lang_for
        assert tesseract_lang_for("en") == "eng"

    def test_empty_falls_back_to_eng(self) -> None:
        from src.ocr_path import tesseract_lang_for
        assert tesseract_lang_for("") == "eng"

    def test_unknown_lang_falls_back_to_eng(self) -> None:
        from src.ocr_path import tesseract_lang_for
        # German, Swedish, French, Spanish — capabilities the README
        # mentions but we don't ship Tesseract data for yet. They must
        # not crash; English is the safe fallback.
        for code in ("sv", "de", "fr", "es", "??"):
            assert tesseract_lang_for(code) == "eng"


class TestCacheKey:
    """_ocr_cache_path produces stable, content-derived paths."""

    def test_same_bytes_yield_same_cache_path(self, tmp_path: Path) -> None:
        a = _write_text_pdf(tmp_path / "a.pdf", text="x")
        b = tmp_path / "b.pdf"
        b.write_bytes(a.read_bytes())
        cache = tmp_path / "cache"
        assert (
            pdf_parser._ocr_cache_path(str(a), cache)
            == pdf_parser._ocr_cache_path(str(b), cache)
        )

    def test_different_bytes_yield_different_cache_path(self, tmp_path: Path) -> None:
        a = _write_text_pdf(tmp_path / "a.pdf", text="content A")
        b = _write_text_pdf(tmp_path / "b.pdf", text="content B")
        cache = tmp_path / "cache"
        assert (
            pdf_parser._ocr_cache_path(str(a), cache)
            != pdf_parser._ocr_cache_path(str(b), cache)
        )

    def test_no_cache_dir_uses_tempdir(self, tmp_path: Path) -> None:
        a = _write_text_pdf(tmp_path / "a.pdf", text="x")
        path = pdf_parser._ocr_cache_path(str(a), None)
        assert str(path).startswith(tempfile.gettempdir())


class TestOCRPathResolver:
    """src.ocr_path is safe to import / call when nothing is bundled."""

    def test_is_ocr_available_returns_bool(self) -> None:
        from src.ocr_path import is_ocr_available
        # Just check it doesn't crash and returns a bool. Actual value
        # depends on whether ocrmypdf / Tesseract are installed locally
        # — CI without the bundling step expects False; a dev box with
        # ocrmypdf + tesseract on PATH expects True.
        assert isinstance(is_ocr_available(), bool)

    def test_setup_ocr_path_is_idempotent(self) -> None:
        from src.ocr_path import setup_ocr_path
        # No-op when nothing is bundled. Must not crash on repeated calls.
        setup_ocr_path()
        setup_ocr_path()
