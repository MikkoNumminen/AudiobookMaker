"""End-to-end integration test for the PDF → MP3 pipeline.

Uses a mock TTS engine to verify the full flow:
  parse PDF → normalize text → chunk → synthesize → combine → output MP3
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import fitz  # PyMuPDF
import pytest

from src.pdf_parser import parse_pdf
from src.tts_base import (
    EngineStatus,
    ProgressCallback,
    TTSEngine,
    Voice,
    _REGISTRY,
    register_engine,
)
from src.tts_engine import combine_audio_files, split_text_into_chunks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_registry():
    """Isolate each test from the real engine registry."""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


def _make_test_pdf(pages: list[str], title: str = "Test Book") -> str:
    """Create a temporary PDF with given page texts. Returns file path."""
    doc = fitz.open()
    for page_text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), page_text, fontsize=12)
    doc.set_metadata({"title": title})
    path = os.path.join(tempfile.gettempdir(), "integration_test.pdf")
    doc.save(path)
    doc.close()
    return path


# ---------------------------------------------------------------------------
# Stub TTS engine that writes a valid silent MP3
# ---------------------------------------------------------------------------

# Minimal valid MP3 frame (MPEG1 Layer3, 128kbps, 44100Hz, ~26ms of silence).
# This is the smallest valid MP3 that pydub can load.
_SILENT_MP3 = (
    b"\xff\xfb\x90\x00" + b"\x00" * 413  # one MPEG audio frame
)


class _StubEngine(TTSEngine):
    """Fake engine that writes a tiny silent MP3 for each synthesis call."""

    id = "stub"
    display_name = "Stub (test only)"
    description = "Silent stub for integration tests."

    def check_status(self) -> EngineStatus:
        return EngineStatus(available=True)

    def list_voices(self, language: str) -> list[Voice]:
        return [Voice(id="stub-voice", display_name="Stub", language=language)]

    def default_voice(self, language: str) -> Optional[str]:
        return "stub-voice"

    def synthesize(
        self,
        text: str,
        output_path: str,
        voice_id: str,
        language: str,
        progress_cb: Optional[ProgressCallback] = None,
        reference_audio: Optional[str] = None,
        voice_description: Optional[str] = None,
    ) -> None:
        if not text or not text.strip():
            raise ValueError("Cannot synthesize empty text.")
        # Write chunks as individual silent MP3s, then combine.
        chunks = split_text_into_chunks(text)
        chunk_paths = []
        with tempfile.TemporaryDirectory(prefix="stub_") as tmp_dir:
            for i, chunk in enumerate(chunks):
                chunk_path = os.path.join(tmp_dir, f"chunk_{i:04d}.mp3")
                with open(chunk_path, "wb") as f:
                    f.write(_SILENT_MP3)
                chunk_paths.append(chunk_path)
                if progress_cb:
                    progress_cb(i + 1, len(chunks), f"Chunk {i + 1}/{len(chunks)}")
            combine_audio_files(chunk_paths, output_path)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

# pydub + ffmpeg is needed for combine_audio_files
try:
    from pydub import AudioSegment

    AudioSegment.silent(duration=10).export(
        os.path.join(tempfile.gettempdir(), "_ffmpeg_check.mp3"), format="mp3"
    )
    _FFMPEG_AVAILABLE = True
except Exception:
    _FFMPEG_AVAILABLE = False


@pytest.mark.skipif(not _FFMPEG_AVAILABLE, reason="ffmpeg not available")
class TestPdfToMp3Pipeline:
    """Full pipeline: PDF → parse → synthesize (stub) → MP3."""

    def test_single_page_pdf_produces_mp3(self, clean_registry: None) -> None:
        register_engine(_StubEngine)
        pdf_path = _make_test_pdf(["Tämä on testilause. Toinen lause seuraa."])
        output_path = os.path.join(tempfile.gettempdir(), "test_output.mp3")
        try:
            book = parse_pdf(pdf_path)
            assert book.total_chars > 0

            engine = _StubEngine()
            engine.synthesize(
                book.full_text, output_path, "stub-voice", "fi"
            )

            assert os.path.exists(output_path)
            assert os.path.getsize(output_path) > 0
        finally:
            for p in (pdf_path, output_path):
                if os.path.exists(p):
                    os.unlink(p)

    def test_multi_page_pdf_produces_mp3(self, clean_registry: None) -> None:
        register_engine(_StubEngine)
        pages = [
            "Ensimmäinen luku. Tässä on tekstiä.",
            "Toinen luku. Lisää sisältöä tässä.",
            "Kolmas luku. Viimeinen sivu.",
        ]
        pdf_path = _make_test_pdf(pages, title="Moniosainen kirja")
        output_path = os.path.join(tempfile.gettempdir(), "test_multi.mp3")
        try:
            book = parse_pdf(pdf_path)
            assert len(book.chapters) >= 1
            assert book.total_chars > 0

            engine = _StubEngine()
            engine.synthesize(
                book.full_text, output_path, "stub-voice", "fi"
            )

            assert os.path.exists(output_path)
            assert os.path.getsize(output_path) > 0
        finally:
            for p in (pdf_path, output_path):
                if os.path.exists(p):
                    os.unlink(p)

    def test_progress_callback_is_called(self, clean_registry: None) -> None:
        register_engine(_StubEngine)
        pdf_path = _make_test_pdf(["Lyhyt testi."])
        output_path = os.path.join(tempfile.gettempdir(), "test_progress.mp3")
        progress_calls: list[tuple[int, int, str]] = []

        def progress_cb(current: int, total: int, msg: str) -> None:
            progress_calls.append((current, total, msg))

        try:
            book = parse_pdf(pdf_path)
            engine = _StubEngine()
            engine.synthesize(
                book.full_text, output_path, "stub-voice", "fi", progress_cb
            )
            assert len(progress_calls) > 0
            # Last call should have current == total
            last = progress_calls[-1]
            assert last[0] == last[1]
        finally:
            for p in (pdf_path, output_path):
                if os.path.exists(p):
                    os.unlink(p)

    def test_empty_text_raises(self, clean_registry: None) -> None:
        register_engine(_StubEngine)
        engine = _StubEngine()
        output_path = os.path.join(tempfile.gettempdir(), "test_empty.mp3")
        with pytest.raises(ValueError, match="empty"):
            engine.synthesize("", output_path, "stub-voice", "fi")

    def test_chunking_preserves_all_text(self) -> None:
        """Verify that split_text_into_chunks doesn't lose content."""
        text = "Ensimmäinen lause. Toinen lause. Kolmas lause on pidempi ja jatkuu."
        chunks = split_text_into_chunks(text)
        reassembled = " ".join(chunks)
        # All original words should appear in chunks
        for word in text.split():
            assert word.rstrip(".") in reassembled or word in reassembled


class TestPipelinePartsNoFfmpeg:
    """Tests that verify pipeline logic without requiring ffmpeg."""

    def test_pdf_parse_to_chunks(self) -> None:
        """PDF → parse → chunk pipeline produces non-empty chunks."""
        pdf_path = _make_test_pdf(["Tämä on testi. Toinen lause."])
        try:
            book = parse_pdf(pdf_path)
            assert book.total_chars > 0
            chunks = split_text_into_chunks(book.full_text)
            assert len(chunks) > 0
            assert all(c.strip() for c in chunks)
        finally:
            os.unlink(pdf_path)

    def test_stub_engine_contract(self, clean_registry: None) -> None:
        """Stub engine satisfies TTSEngine interface."""
        register_engine(_StubEngine)
        engine = _StubEngine()
        assert engine.check_status().available
        voices = engine.list_voices("fi")
        assert len(voices) == 1
        assert engine.default_voice("fi") == "stub-voice"
