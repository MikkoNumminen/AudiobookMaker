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
exactly the signal that separates a bare title from "Loppu." So
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


# Invented for this test. Deliberately a mundane subject with no source:
# the shapes under test are "short unpunctuated line" and "paragraph", and
# anything that reads like an excerpt from a real book has no place in a
# tracked fixture (CLAUDE.md, third-party material).
_BOOK = """Luku 6 puutarhanhoito

Puutarha jakautuu kahteen osaan. Toinen niistä on kasvimaa.

Kasteluohjeet

Kastelu kannattaa tehdä aamulla. Silloin haihtuminen on vähäisintä."""


def _prepare(runner, text, chunk_chars=200):
    chapter = SimpleNamespace(content=text, title="Text", index=0)
    return runner._prepare_chapter_chunks(chapter, chunk_chars, 0, language="fi")


class TestHeadingDetectionOnRawText:
    @pytest.mark.parametrize("text", [
        "Kasteluohjeet",
        "Luku 6 puutarhanhoito",
        "6. Esineoikeus",
        "KASTELUOHJEET",
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
        blocks = [("Luku 6 puutarhanhoito", True), ("Leipätekstiä tässä.", False)]
        chunks, heads = split_blocks_into_chunks(blocks, 200, 60)
        assert chunks[0] == "Luku 6 puutarhanhoito"
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
        assert chunks[0].startswith("Luku kuusi puutarhanhoito")
        assert "jakautuu" not in chunks[0]

    def test_every_heading_is_flagged(self, runner):
        chunks, heads = _prepare(runner, _BOOK)
        flagged = [chunks[i] for i in sorted(heads)]
        assert len(flagged) == 2
        assert any("puutarhanhoito" in f.lower() for f in flagged)
        assert any("Kasteluohjeet" in f for f in flagged)

    def test_body_sentences_are_not_flagged(self, runner):
        chunks, heads = _prepare(runner, _BOOK)
        for i, c in enumerate(chunks):
            if "jakautuu" in c or "aamulla" in c:
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
        # Assert the EXACT delta, not merely "longer". A greater-than check
        # passes for a 1 ms gap, and passes just as happily if the pause is
        # applied on only one side of the title — both of which are the
        # regressions worth catching.
        #
        # Only one seam changes: the one after chunk 0. Unflagged, a bare
        # title ends on a word and classifies as "mid", so its baseline gap is
        # ZERO -- a heading is otherwise pulled TIGHTER against the body than
        # an ordinary sentence, which is the opposite of what it needs.
        expected = runner.HEADING_SEAM_GAP_MS - 0
        assert len(with_heading) - len(without) == expected

    def test_a_heading_in_the_middle_widens_both_of_its_seams(self, runner):
        """The pause before a title matters as much as the one after it."""
        pytest.importorskip("pydub")
        texts = ["Eka kappale.", "Otsikko", "Toka kappale."]
        framed = runner._assemble_chunks(
            (self._seg() for _ in texts), texts, headings={1},
        )
        plain = runner._assemble_chunks(
            (self._seg() for _ in texts), texts, headings=set(),
        )
        # Two seams change: the one BEFORE the title (sentence-tier ->
        # heading) and the one after it (mid -> heading).
        expected = (
            (runner.HEADING_SEAM_GAP_MS - runner.SENTENCE_SEAM_GAP_MS)
            + (runner.HEADING_SEAM_GAP_MS - 0)
        )
        assert len(framed) - len(plain) == expected

    def test_heading_indices_are_chapter_absolute_in_a_split_part(self, runner):
        """`_assemble_chapter_parts` passes chapter-absolute indices while the
        segment iterator is part-relative. That is the classic off-by-offset
        spot, and a heading landing on a part boundary decides which MP3 the
        900 ms ends up in."""
        pytest.importorskip("pydub")
        texts = ["Eka.", "Toka.", "Otsikko", "Kolmas.", "Neljas."]
        # Assemble only the second half, where chunk 2 (absolute) is a heading.
        part = runner._assemble_chunks(
            (self._seg() for _ in texts[2:]), texts,
            index_offset=2, total=len(texts), headings={2},
        )
        no_heads = runner._assemble_chunks(
            (self._seg() for _ in texts[2:]), texts,
            index_offset=2, total=len(texts), headings=set(),
        )
        # Chunk 2 is the heading and opens this part, so exactly the seam
        # after it widens, from the mid-tier zero. If the offset were
        # mishandled the flag would land on a different chunk and the delta
        # would come out as a sentence-tier difference instead.
        expected = runner.HEADING_SEAM_GAP_MS - 0
        assert len(part) - len(no_heads) == expected

    def test_no_headings_matches_previous_behaviour(self, runner):
        pytest.importorskip("pydub")
        texts = ["Eka.", "Toka.", "Kolmas."]
        a = runner._assemble_chunks((self._seg() for _ in texts), texts)
        b = runner._assemble_chunks(
            (self._seg() for _ in texts), texts, headings=set(),
        )
        assert a.raw_data == b.raw_data


class TestBothLanguagesBehaveTheSame:
    """The heading logic is language-agnostic and must stay that way.

    Nothing in the chunker knows about language, and `terminate_paragraphs`
    runs BEFORE the per-language dispatch, so both pipelines get it. The one
    place this could silently diverge is block alignment: classification
    happens on the raw text and chunking on the normalized text, so if one
    language's normalizer ever stopped preserving blank lines, its block
    indices would stop lining up and the code would fall back to no headings
    — correct, but silently pause-less in that language only.
    """

    _EN = (
        "Chapter 6 Property Law\n\n"
        "Property law falls into two parts. One of them is ownership.\n\n"
        "Assets And Claims\n\n"
        "This area is broad. It covers both objects and receivables."
    )
    _FI = _BOOK

    @pytest.mark.parametrize("lang,text", [("en", _EN), ("fi", _FI)])
    def test_blank_lines_survive_normalization(self, lang, text):
        """The load-bearing assumption behind raw/normalized block alignment."""
        from src.tts_normalizer import normalize_text
        assert len(split_into_blocks(normalize_text(text, lang))) == len(
            split_into_blocks(text)
        )

    @pytest.mark.parametrize("lang,text", [("en", _EN), ("fi", _FI)])
    def test_headings_are_found(self, lang, text):
        chapter = SimpleNamespace(content=text, title="T", index=0)
        # Imported lazily inside the runner fixture's module scope.
        import importlib.util
        path = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "generate_chatterbox_audiobook.py"
        )
        spec = importlib.util.spec_from_file_location(f"_r_{lang}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"_r_{lang}"] = mod
        spec.loader.exec_module(mod)

        chunks, heads = mod._prepare_chapter_chunks(
            chapter, 200, 0, language=lang
        )
        assert len(heads) == 2, f"{lang}: expected both headings, got {heads}"
        assert 0 in heads, f"{lang}: the opening title was not flagged"

    def test_english_and_finnish_flag_the_same_positions(self, runner):
        """Same document shape in either language must give the same seams."""
        en = runner._prepare_chapter_chunks(
            SimpleNamespace(content=self._EN, title="T", index=0),
            200, 0, language="en",
        )
        fi = runner._prepare_chapter_chunks(
            SimpleNamespace(content=self._FI, title="T", index=0),
            200, 0, language="fi",
        )
        assert en[1] == fi[1], "heading positions diverged between languages"
