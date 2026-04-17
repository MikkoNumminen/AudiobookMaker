"""Tests for src.synthesis_orchestrator.

The orchestrator is the first module extracted from the UnifiedApp
god-object. Its helpers have zero GUI dependencies, so they can be
unit-tested directly — no tkinter, no fixtures, no real engine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.synthesis_orchestrator import (
    default_output_dir,
    next_available_numbered_path,
    parse_book,
    suggest_output_path,
)


# ---------------------------------------------------------------------------
# parse_book — extension dispatch
# ---------------------------------------------------------------------------


def test_parse_book_txt_round_trip(tmp_path: Path):
    src = tmp_path / "my_book.txt"
    src.write_text("Hello world.\nSecond line.", encoding="utf-8")

    book = parse_book(str(src))

    assert book.metadata.title == "My Book"  # underscore → space + Title Case
    assert book.metadata.num_pages == 1
    assert len(book.chapters) == 1
    assert "Hello world." in book.chapters[0].content


def test_parse_book_txt_handles_non_utf8_bytes(tmp_path: Path):
    # Latin-1 bytes that are not valid UTF-8; parser uses errors="replace".
    src = tmp_path / "legacy.txt"
    src.write_bytes(b"caf\xe9 au lait")

    book = parse_book(str(src))

    # Implementation uses errors="replace" — content is readable, no crash.
    assert book.chapters[0].content  # non-empty


def test_parse_book_unknown_ext_falls_through_to_pdf(tmp_path: Path):
    """Unknown extensions default to the PDF parser so the error message
    stays familiar. We expect a parser-side failure, not a local crash."""
    src = tmp_path / "mystery.xyz"
    src.write_text("not a real pdf", encoding="utf-8")

    with pytest.raises(Exception):
        parse_book(str(src))


# ---------------------------------------------------------------------------
# default_output_dir
# ---------------------------------------------------------------------------


def test_default_output_dir_dev_mode(monkeypatch: pytest.MonkeyPatch):
    # Dev mode: not frozen. Should return Documents/AudiobookMaker.
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    result = default_output_dir()

    assert result.name == "AudiobookMaker"
    assert result.parent.name == "Documents"


def test_default_output_dir_frozen_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # Simulate a PyInstaller bundle: frozen + executable under tmp_path.
    fake_exe = tmp_path / "AudiobookMaker.exe"
    fake_exe.write_bytes(b"")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)

    result = default_output_dir()

    assert result == tmp_path.resolve()


# ---------------------------------------------------------------------------
# suggest_output_path
# ---------------------------------------------------------------------------


def test_suggest_output_path_pdf_mode_sibling(tmp_path: Path):
    pdf = tmp_path / "my_book.pdf"
    pdf.write_bytes(b"")

    result = suggest_output_path("pdf", str(pdf))

    assert Path(result) == pdf.with_suffix(".mp3")


def test_suggest_output_path_pdf_mode_preserves_directory(tmp_path: Path):
    # PDF in a nested folder → MP3 lands in the same folder.
    nested = tmp_path / "sub" / "dir"
    nested.mkdir(parents=True)
    pdf = nested / "title.pdf"
    pdf.write_bytes(b"")

    result = suggest_output_path("pdf", str(pdf))

    assert Path(result).parent == nested


def test_suggest_output_path_text_mode_auto_increments(tmp_path: Path):
    # Fresh dir → _1.
    assert suggest_output_path("text", None, out_dir=tmp_path).endswith(
        "texttospeech_1.mp3"
    )

    # _1 exists → _2.
    (tmp_path / "texttospeech_1.mp3").write_bytes(b"")
    assert suggest_output_path("text", None, out_dir=tmp_path).endswith(
        "texttospeech_2.mp3"
    )

    # _1 and _2 exist → _3.
    (tmp_path / "texttospeech_2.mp3").write_bytes(b"")
    assert suggest_output_path("text", None, out_dir=tmp_path).endswith(
        "texttospeech_3.mp3"
    )


def test_suggest_output_path_creates_missing_dir(tmp_path: Path):
    target_dir = tmp_path / "new" / "nested" / "dir"
    assert not target_dir.exists()

    suggest_output_path("text", None, out_dir=target_dir)

    assert target_dir.exists()  # helper created it


def test_suggest_output_path_text_mode_no_pdf_path(tmp_path: Path):
    """Even with input_mode='pdf', a None pdf_path falls back to text mode."""
    result = suggest_output_path("pdf", None, out_dir=tmp_path)
    assert result.endswith("texttospeech_1.mp3")


# ---------------------------------------------------------------------------
# next_available_numbered_path
# ---------------------------------------------------------------------------


def test_bump_path_returns_unchanged_when_file_missing(tmp_path: Path):
    fresh = tmp_path / "book.mp3"
    assert next_available_numbered_path(str(fresh)) == str(fresh)


def test_bump_path_numbered_stem_increments(tmp_path: Path):
    existing = tmp_path / "texttospeech_3.mp3"
    existing.write_bytes(b"")

    result = next_available_numbered_path(str(existing))

    assert Path(result).name == "texttospeech_4.mp3"


def test_bump_path_plain_stem_adds_suffix_2(tmp_path: Path):
    existing = tmp_path / "book.mp3"
    existing.write_bytes(b"")

    result = next_available_numbered_path(str(existing))

    assert Path(result).name == "book_2.mp3"


def test_bump_path_skips_over_existing_higher_numbers(tmp_path: Path):
    (tmp_path / "book.mp3").write_bytes(b"")
    (tmp_path / "book_2.mp3").write_bytes(b"")
    (tmp_path / "book_3.mp3").write_bytes(b"")

    result = next_available_numbered_path(str(tmp_path / "book.mp3"))

    assert Path(result).name == "book_4.mp3"


def test_bump_path_numbered_stem_skips_existing(tmp_path: Path):
    (tmp_path / "book_5.mp3").write_bytes(b"")
    (tmp_path / "book_6.mp3").write_bytes(b"")

    result = next_available_numbered_path(str(tmp_path / "book_5.mp3"))

    assert Path(result).name == "book_7.mp3"


def test_bump_path_preserves_extension(tmp_path: Path):
    existing = tmp_path / "clip.wav"
    existing.write_bytes(b"")

    result = next_available_numbered_path(str(existing))

    assert Path(result).suffix == ".wav"


def test_bump_path_handles_extensionless_input(tmp_path: Path):
    existing = tmp_path / "noext"
    existing.write_bytes(b"")

    result = next_available_numbered_path(str(existing))

    # Implementation defaults to .mp3 when there's no suffix.
    assert Path(result).name == "noext_2.mp3"
