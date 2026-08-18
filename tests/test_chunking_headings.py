"""Tests for hearing where a new section starts.

Reported from the field (2026-08-18): the narrator reads a chapter title and
runs straight on into the body, so a listener cannot tell a new chapter began.
"Kun nyt se vetää kokonaisuuden ja sit alkaa tyyliin uus kappale kirjaa ni
vetää putkeen siihen uuden otsikon ja alkaa rallatus."

Two things were wrong. `_split_sentences` ignored blank lines, so a heading and
the sentence after it became ONE unit and there was no seam to pause at. And
`terminate_paragraphs` (an earlier fix for the same report) made the heading a
sentence, after which `_merge_short_chunks` folded it straight back into the
body -- the real run produced a single chunk for a four-paragraph document.

The subtle part is WHERE a heading can be recognised. `terminate_paragraphs`
appends a full stop to every unpunctuated paragraph-final line, which is
exactly the signal that separates "Varallisuusoikeus" from "Loppu." So
classification has to happen on the RAW text and be carried forward; done
after normalization, every short paragraph in the book earns a section pause.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.tts_chunking import (
    HEADING_MAX_CHARS,
    looks_like_heading,
    split_blocks_into_chunks,
    split_into_blocks,
)


@pytest.fixture(scope="module")
def runner():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "generate_chatterbox_audiobook.py"
    )
    spec = importlib.util.spec_from_file_location("_abm_runner_head", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_abm_runner_head"] = mod
    spec.loader.exec_module(mod)
    return mod


_BOOK = """Luku 6 esineoikeus

Esineoikeus jakautuu kahteen osaan. Toinen niistä on omistusoikeus.

Varallisuusoikeus

Varallisuusoikeus on laaja ala. Se kattaa monta asiaa."""


def _prepare(runner, text, chunk_chars=200):
    chapter = SimpleNamespace(content=text, title="Text", index=0)
    return runner._prepare_chapter_chunks(chapter, chunk_chars, 0, language="fi")


class TestHeadingDetectionOnRawText:
    @pytest.mark.parametrize("text", [
        "Varallisuusoikeus",
        "Luku 6 esineoikeus",
        "6. Esineoikeus",
        "VARALLISUUSOIKEUS",
        "Tauot lauseiden välillä",
    ])
    def test_headings(self, text):
        assert looks_like_heading(text)

    @pytest.mark.parametrize("text", [
        "Tämä on tavallinen virke.",   # carries its own terminator
        "Loppu.",                       # a one-word paragraph is not a heading
        "Kysymys?",
        "ja niin edelleen,",
        "bareword",                     # lower-case: a force-split leftover
        "",
        "   ",
        "123 456",                      # no letters
        "Otsikko\nkahdella rivillä",
    ])
    def test_not_headings(self, text):
        assert not looks_like_heading(text)

    def test_long_unpunctuated_text_is_prose(self):
        assert not looks_like_heading("Sana " * 40)

    def test_length_limit_boundary(self):
        assert looks_like_heading("A" + "b" * (HEADING_MAX_CHARS - 1))
        assert not looks_like_heading("A" + "b" * HEADING_MAX_CHARS)

    def test_a_one_sentence_paragraph_is_not_a_heading(self):
        """The distinction that forced classification onto the raw text.
        After terminate_paragraphs this and a real heading are identical."""
        assert not looks_like_heading("Se on laaja ala.")


class TestBlockChunking:
    def test_a_heading_becomes_its_own_chunk(self):
        blocks = [("Luku 6 esineoikeus", True), ("Leipätekstiä tässä.", False)]
        chunks, heads = split_blocks_into_chunks(blocks, 200, 60)
        assert chunks[0] == "Luku 6 esineoikeus"
        assert heads == {0}

    def test_a_short_heading_is_not_folded_into_the_body(self):
        """min_chars folds short chunks into neighbours; that would undo it."""
        blocks = [("Otsikko", True), ("Lyhyt.", False)]
        chunks, heads = split_blocks_into_chunks(blocks, 200, 200)
        assert "Otsikko" in chunks
        assert heads == {0}

    def test_non_heading_blocks_still_pack_together(self):
        """Chunking each paragraph alone strands short ones, and Chatterbox
        rambles for ten seconds on a tiny input."""
        blocks = [("Eka kappale.", False), ("Toka kappale.", False)]
        chunks, heads = split_blocks_into_chunks(blocks, 200, 60)
        assert len(chunks) == 1
        assert heads == set()

    def test_empty_blocks_are_skipped(self):
        chunks, heads = split_blocks_into_chunks([("", True), ("  ", False)], 200, 60)
        assert chunks == []
        assert heads == set()

    def test_split_into_blocks_uses_blank_lines(self):
        assert split_into_blocks("A\n\nB") == ["A", "B"]

    def test_wrapped_lines_are_one_block(self):
        """A single newline is line wrapping, not a paragraph break."""
        assert split_into_blocks("yksi\nkappale") == ["yksi\nkappale"]


class TestEndToEndThroughTheRunner:
    def test_the_reported_defect_is_gone(self, runner):
        """The heading used to be glued to the first body sentence, and the
        whole document collapsed into one chunk."""
        chunks, heads = _prepare(runner, _BOOK)
        assert len(chunks) > 1
        assert 0 in heads
        assert chunks[0].startswith("Luku kuusi esineoikeus")
        assert "jakautuu" not in chunks[0]

    def test_every_heading_is_flagged(self, runner):
        chunks, heads = _prepare(runner, _BOOK)
        flagged = [chunks[i] for i in sorted(heads)]
        assert len(flagged) == 2
        assert any("esineoikeus" in f.lower() for f in flagged)
        assert any("Varallisuusoikeus" in f for f in flagged)

    def test_body_sentences_are_not_flagged(self, runner):
        chunks, heads = _prepare(runner, _BOOK)
        for i, c in enumerate(chunks):
            if "jakautuu" in c or "laaja ala" in c:
                assert i not in heads, c

    def test_a_document_with_no_headings_is_unaffected(self, runner):
        chunks, heads = _prepare(
            runner, "Eka virke tässä. Toka virke tässä.\n\nToinen kappale tässä."
        )
        assert heads == set()


class TestPauseAroundHeadings:
    def test_a_heading_is_framed_by_silence(self, runner):
        chunks, heads = _prepare(runner, _BOOK)
        # After the title.
        assert runner._seam_gap_ms(
            chunks[0], is_heading=True, next_is_heading=False
        ) == runner.HEADING_SEAM_GAP_MS
        # And before the next one, or the paragraph runs into it.
        assert runner._seam_gap_ms(
            chunks[1], is_heading=False, next_is_heading=True
        ) == runner.HEADING_SEAM_GAP_MS

    def test_heading_pause_is_clearly_longer_than_a_full_stop(self, runner):
        """Equal to a sentence break and the listener hears no section change.
        Audiobooks mark one with a pause and nothing else."""
        assert runner.HEADING_SEAM_GAP_MS > runner.SENTENCE_SEAM_GAP_MS * 2

    def test_ordinary_seams_are_unchanged(self, runner):
        assert runner._seam_gap_ms("Virke päättyy.") == runner.SENTENCE_SEAM_GAP_MS
        assert runner._seam_gap_ms("lauseke,") == runner.CLAUSE_SEAM_GAP_MS
        assert runner._seam_gap_ms("kesken jäänyt") == 0

    def test_heading_flags_default_off(self, runner):
        """A caller that passes neither flag gets the old behaviour."""
        assert runner._seam_gap_ms("Otsikko") == 0


class TestAssemblyUsesTheFlags:
    def _seg(self, ms=200):
        pytest.importorskip("pydub")
        from pydub import AudioSegment
        return AudioSegment.silent(
            duration=ms, frame_rate=24000
        ).set_sample_width(2)

    def test_heading_gap_reaches_the_audio(self, runner):
        pytest.importorskip("pydub")
        texts = ["Otsikko", "Leipätekstiä.", "Lisää tekstiä."]
        with_heading = runner._assemble_chunks(
            (self._seg() for _ in texts), texts, headings={0},
        )
        without = runner._assemble_chunks(
            (self._seg() for _ in texts), texts, headings=set(),
        )
        # The heading seam is longer than the sentence seam it replaces.
        assert len(with_heading) > len(without)

    def test_no_headings_matches_previous_behaviour(self, runner):
        pytest.importorskip("pydub")
        texts = ["Eka.", "Toka.", "Kolmas."]
        a = runner._assemble_chunks((self._seg() for _ in texts), texts)
        b = runner._assemble_chunks(
            (self._seg() for _ in texts), texts, headings=set(),
        )
        assert a.raw_data == b.raw_data
