"""Regressions found reviewing the heading-pause work.

The first cut looked correct on a hand-written .txt and did nothing at all for
the formats users actually convert, while introducing two hazards of its own:
tiny heading chunks that trip the synthesis band guard, and a chunk plan that
changed under an index-keyed cache that resume reuses by default.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

from src.tts_chunking import (
    classify_heading_blocks,
    looks_like_heading,
    split_into_blocks,
)


@pytest.fixture(scope="module")
def runner():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "generate_chatterbox_audiobook.py"
    )
    spec = importlib.util.spec_from_file_location("_abm_runner_review", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_abm_runner_review"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestCrlfInput:
    def test_crlf_still_finds_blocks(self):
        """A single-newline blank-line regex cannot match a CRLF blank line:
        the carriage return between the two newlines is neither a space nor a
        tab. The whole feature vanished on CRLF files, with nothing logged."""
        crlf = "Otsikko\r\n\r\nLeipatekstia.\r\n\r\nToinen otsikko\r\n\r\nLisaa."
        assert len(split_into_blocks(crlf)) == 4

    def test_crlf_headings_are_classified(self):
        crlf = "Otsikko\r\n\r\nLeipatekstia tassa.\r\n\r\nToinen\r\n\r\nLisaa."
        assert classify_heading_blocks(split_into_blocks(crlf))[0] is True


class TestClosingWrappers:
    @pytest.mark.parametrize("text", [
        'Han sanoi: "Ei koskaan."',
        "Se oli hyva (niin sanottiin.)",
        "Se paattyi tahan.»",
    ])
    def test_a_closing_quote_does_not_make_a_sentence_a_heading(self, text):
        """The final character is the quote mark, not the full stop, so the
        terminator check passed and every line of dialogue became a heading.
        The seam classifier in the runner already looks through the same
        wrappers for the very same question."""
        assert not looks_like_heading(text)


class TestListsAreNotHeadings:
    def test_a_list_is_not_a_stack_of_headings(self):
        """Every item is short, capitalised and unterminated, exactly like a
        title. Pausing 900 ms either side reads a four-item list with nearly
        two seconds of dead air between entries."""
        blocks = ["Ostoslista", "Omenat", "Paarynat", "Banaanit", "Ja edelleen."]
        assert not any(classify_heading_blocks(blocks))

    def test_a_number_above_its_title_still_counts(self):
        """Two in a row is a real and common way to write a chapter opening."""
        flags = classify_heading_blocks(["Luku 6", "Puutarhanhoito", "Teksti alkaa."])
        assert flags[:2] == [True, True]

    def test_a_real_heading_between_paragraphs_survives(self):
        flags = classify_heading_blocks(
            ["Eka kappale tassa.", "Otsikko", "Toka kappale tassa."]
        )
        assert flags == [False, True, False]


class TestCacheSurvivesAPlanChange:
    """The cache is content-addressed, so changing the chunk plan no longer
    throws away work.

    It used to be keyed by INDEX, which made it worthless the moment chunking
    changed: every index moved, so the whole cache was discarded (hours of GPU
    time) or — before the plan fingerprint landed — old audio was silently
    reused for different words.
    """

    def test_inserting_a_heading_keeps_every_existing_key(self, runner):
        """The scenario that motivated this: a heading appears at the top of a
        chapter, every chunk shifts down one, and nothing needs re-synthesizing
        except the heading itself."""
        before = runner._chunk_keys(["Eka.", "Toka.", "Kolmas."], "fi", "")
        after = runner._chunk_keys(
            ["Otsikko", "Eka.", "Toka.", "Kolmas."], "fi", ""
        )
        assert after[1:] == before

    def test_changed_text_changes_only_that_key(self, runner):
        a = runner._chunk_keys(["Eka.", "Toka."], "fi", "")
        b = runner._chunk_keys(["Eka.", "Muutettu."], "fi", "")
        assert a[0] == b[0]
        assert a[1] != b[1]

    def test_language_is_part_of_the_key(self, runner):
        assert runner._chunk_keys(["Eka."], "fi", "")[0] !=             runner._chunk_keys(["Eka."], "en", "")[0]

    def test_voice_is_part_of_the_key(self, runner):
        """Otherwise switching voice pack hands back audio in the old voice."""
        assert runner._chunk_keys(["Eka."], "fi", "pack-a")[0] !=             runner._chunk_keys(["Eka."], "fi", "pack-b")[0]

    def test_repeated_text_gets_separate_entries(self, runner):
        """Two identical sentences must not share one file: the sweep
        re-rolling one would silently change the other, and a repeated refrain
        would play the byte-identical take every time."""
        keys = runner._chunk_keys(["Sama.", "Eri.", "Sama."], "fi", "")
        assert len(set(keys)) == 3

    def test_repeated_text_keys_are_still_position_independent(self, runner):
        before = runner._chunk_keys(["Sama.", "Eri.", "Sama."], "fi", "")
        after = runner._chunk_keys(["Otsikko", "Sama.", "Eri.", "Sama."], "fi", "")
        assert after[1:] == before

    def test_the_filename_carries_the_key(self, runner, tmp_path):
        key = runner._chunk_keys(["Eka."], "fi", "")[0]
        assert runner._chunk_cache_path(tmp_path, key).name == f"chunk_{key}.wav"

    def test_voice_key_covers_pack_and_reference_clip(self, runner):
        from types import SimpleNamespace
        a = runner._voice_key(SimpleNamespace(voice_pack="p", ref_audio=None))
        b = runner._voice_key(SimpleNamespace(voice_pack=None, ref_audio="r"))
        c = runner._voice_key(SimpleNamespace(voice_pack=None, ref_audio=None))
        assert len({a, b, c}) == 3


class TestLegacyCacheMigration:
    def test_index_named_chunks_are_discarded_once(self, runner, tmp_path):
        """Their filenames record a position, and the plan that produced those
        positions was never stored, so they cannot be matched to content keys.
        A one-time loss on upgrade, and the reason the scheme changed is so it
        is the last one."""
        chunks_dir = tmp_path / ".chunks"
        chunks_dir.mkdir()
        for i in range(3):
            (chunks_dir / f"ch01_chunk{i:04d}.wav").write_bytes(b"old")
        assert runner._discard_legacy_index_cache(chunks_dir) == 3
        assert list(chunks_dir.glob("*.wav")) == []

    def test_content_keyed_chunks_are_left_alone(self, runner, tmp_path):
        chunks_dir = tmp_path / ".chunks"
        chunks_dir.mkdir()
        key = runner._chunk_keys(["Eka."], "fi", "")[0]
        runner._chunk_cache_path(chunks_dir, key).write_bytes(b"keep")
        assert runner._discard_legacy_index_cache(chunks_dir) == 0
        assert len(list(chunks_dir.glob("*.wav"))) == 1

    def test_an_empty_directory_is_fine(self, runner, tmp_path):
        chunks_dir = tmp_path / ".chunks"
        chunks_dir.mkdir()
        assert runner._discard_legacy_index_cache(chunks_dir) == 0


class TestFormatCoverage:
    """Which input shapes reach the chunker with their blocks intact.

    Both of these were silently uncovered: the feature looked right on a
    hand-written .txt and did nothing for the formats users convert.
    """

    _BLOCK_TAGS = (
        "h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre",
    )

    def test_epub_extraction_keeps_block_boundaries(self):
        pytest.importorskip("bs4")
        from bs4 import BeautifulSoup
        html = (
            "<html><body><h2>Luku 6</h2><p>Eka kappale.</p>"
            "<p>Toka kappale.</p></body></html>"
        )
        soup = BeautifulSoup(html, "html.parser")
        blocks = [
            el.get_text(separator=" ", strip=True)
            for el in soup.find_all(self._BLOCK_TAGS)
        ]
        assert len(split_into_blocks("\n\n".join(blocks))) == 3

    def test_epub_nested_wrappers_do_not_duplicate(self):
        """A div wrapping paragraphs would yield its own text AND each
        child, repeating the whole chapter. div is excluded for that reason."""
        pytest.importorskip("bs4")
        from bs4 import BeautifulSoup
        html = "<div><p>Eka kappale.</p><p>Toka kappale.</p></div>"
        soup = BeautifulSoup(html, "html.parser")
        blocks = [
            el.get_text(separator=" ", strip=True)
            for el in soup.find_all(self._BLOCK_TAGS)
        ]
        assert blocks == ["Eka kappale.", "Toka kappale."]

    def test_docx_paragraphs_survive_the_shared_cleaner(self):
        """The no-heading-styles path joins paragraphs, then the shared PDF
        cleaner turns lone newlines into spaces."""
        from src.pdf_parser import clean_text
        joined = "\n\n".join(["Otsikko", "Eka kappale.", "Toka kappale."])
        assert len(split_into_blocks(clean_text(joined))) == 3

    def test_single_newline_join_would_not_survive(self):
        """Pins WHY the join had to change, so nobody reverts it."""
        from src.pdf_parser import clean_text
        joined = "\n".join(["Otsikko", "Eka kappale.", "Toka kappale."])
        assert len(split_into_blocks(clean_text(joined))) == 1


class TestShortUtterancesAreJudgedFairly:
    """The band guard's rambling ceiling carries a fixed overhead now.

    Every utterance costs the model some lead-in and tail regardless of
    length, so a pure seconds-per-character ceiling is far too strict on
    anything short. Both a heading and a one-line paragraph stranded between
    two headings failed it, burned all five re-rolls, and shipped with a
    STILL-rambling warning.
    """

    def test_a_five_char_heading_passes(self, runner):
        # 1.5 s over 5 chars is 0.30 s/char, three times the per-char ceiling,
        # and completely normal for a spoken title.
        assert runner._ratio_badness(1.5, 5) == 0.0

    def test_a_short_stranded_paragraph_passes(self, runner):
        """A heading forces a block boundary, so a lone short paragraph
        between two headings cannot be folded into a neighbour and arrives
        as its own small chunk."""
        assert runner._ratio_badness(2.0, 21) == 0.0

    def test_a_tiny_rambler_is_still_caught(self, runner):
        """The blanket sub-floor exemption was the ORIGINAL bug: it let tiny
        ramblers ship unchecked. The overhead must not reintroduce it."""
        assert runner._ratio_badness(5.0, 7) > 0.0
        assert runner._ratio_badness(12.0, 7) > 0.0

    def test_long_chunks_are_effectively_unchanged(self, runner):
        """On a 300-char chunk the overhead moves the ceiling by one second
        out of sixty, so established behaviour is untouched."""
        assert runner._ratio_badness(20.0, 300) == 0.0
        assert runner._ratio_badness(70.0, 300) > 0.0

    def test_the_truncation_edge_still_works(self, runner):
        assert runner._ratio_badness(5.0, 300) > 0.0

    def test_no_heading_special_case_remains(self, runner):
        """The overhead model covers headings and short paragraphs alike, so
        the guard needs no knowledge of which chunks are titles."""
        import inspect
        assert "is_heading" not in inspect.signature(runner._ratio_badness).parameters
        assert "is_heading" not in inspect.signature(
            runner._cached_chunk_healthy
        ).parameters


class TestOneHeadingLengthConstant:
    def test_the_two_classifiers_share_it(self):
        """Both answer related questions about the same documents and both
        declared 80 independently, which would have drifted the first time
        either was tuned."""
        from src.pdf_parser import _MAX_HEADING_LEN
        from src.tts_chunking import HEADING_MAX_CHARS
        assert _MAX_HEADING_LEN is HEADING_MAX_CHARS

    def test_the_two_classifiers_answer_different_questions(self):
        """A PDF chapter heading is consumed into Chapter.title and dropped
        from the body, so it is never narrated; the chunker's detector only
        ever sees the sub-headings the PDF parser did not match. Pinning the
        difference so nobody 'unifies' them into one rule by mistake."""
        from src.pdf_parser import _looks_like_heading as pdf_heading
        from src.tts_chunking import looks_like_heading as chunk_heading
        # A plain sub-heading: not a chapter opener, but is a heading to pause at.
        assert not pdf_heading("Kasteluohjeet")
        assert chunk_heading("Kasteluohjeet")


class TestCacheReviewRegressions:
    """Defects found reviewing the content-addressed cache."""

    def test_the_same_text_in_two_chapters_gets_two_keys(self, runner):
        """The occurrence counter was per chapter, so a repeated epigraph in
        chapters 1 and 7 mapped to ONE file. Chapter 7's median sweep could
        re-roll it and overwrite the audio chapter 1 assembles from."""
        seen = {}
        ch1 = runner._chunk_keys(["Toistuva.", "Eka."], "fi", "", seen=seen)
        ch7 = runner._chunk_keys(["Toistuva.", "Muu."], "fi", "", seen=seen)
        assert ch1[0] != ch7[0]
        assert len({*ch1, *ch7}) == 4

    def test_a_retrained_voice_pack_changes_the_key(self, runner, tmp_path):
        """A pack is normally re-trained IN PLACE. Hashing only its path left
        the key identical and the whole book shipped in the previous voice."""
        import time
        from types import SimpleNamespace

        pack = tmp_path / "pack"
        pack.mkdir()
        (pack / "adapter.bin").write_bytes(b"v1")
        args = SimpleNamespace(voice_pack=str(pack), ref_audio=None)
        before = runner._voice_key(args)

        time.sleep(1.1)
        (pack / "adapter.bin").write_bytes(b"v2-retrained-and-larger")
        assert runner._voice_key(args) != before

    def test_the_same_pack_is_stable(self, runner, tmp_path):
        from types import SimpleNamespace
        pack = tmp_path / "pack"
        pack.mkdir()
        (pack / "adapter.bin").write_bytes(b"v1")
        args = SimpleNamespace(voice_pack=str(pack), ref_audio=None)
        assert runner._voice_key(args) == runner._voice_key(args)

    def test_sampling_constants_are_in_the_key(self, runner):
        """Retune them and a resumed book is half old renders, half new,
        spliced into one chapter with an audible change of pace."""
        from types import SimpleNamespace
        args = SimpleNamespace(voice_pack=None, ref_audio=None)
        key = runner._voice_key(args)
        assert str(runner.FI_TEMPERATURE) in key
        assert str(runner.FI_EXAGGERATION) in key
        assert str(runner.FI_CFG_WEIGHT) in key

    def test_orphaned_chunks_are_swept(self, runner, tmp_path):
        """Content addressing abandons superseded chunks rather than
        overwriting them, so without a sweep the directory grows forever."""
        chunks_dir = tmp_path / ".chunks"
        chunks_dir.mkdir()
        live = runner._chunk_keys(["Eka.", "Toka."], "fi", "")
        for key in live:
            runner._chunk_cache_path(chunks_dir, key).write_bytes(b"keep")
        runner._chunk_cache_path(chunks_dir, "deadbeef" * 4).write_bytes(b"old")

        removed = runner._discard_orphaned_chunks(chunks_dir, set(live))
        assert removed == 1
        assert len(list(chunks_dir.glob("chunk_*.wav"))) == 2

    def test_the_sweep_keeps_everything_still_referenced(self, runner, tmp_path):
        chunks_dir = tmp_path / ".chunks"
        chunks_dir.mkdir()
        live = runner._chunk_keys(["Eka."], "fi", "")
        runner._chunk_cache_path(chunks_dir, live[0]).write_bytes(b"keep")
        assert runner._discard_orphaned_chunks(chunks_dir, set(live)) == 0

    def test_legacy_cleanup_counts_only_successful_deletions(self, runner, tmp_path):
        """Counting attempts meant a locked file was reported as discarded and
        re-reported on every future run."""
        chunks_dir = tmp_path / ".chunks"
        chunks_dir.mkdir()
        (chunks_dir / "ch01_chunk0000.wav").write_bytes(b"old")
        assert runner._discard_legacy_index_cache(chunks_dir) == 1
        assert runner._discard_legacy_index_cache(chunks_dir) == 0


class TestVerifiersFindContentKeyedChunks:
    """The transcript check is the completion gate for every narration. When
    the cache scheme changed, both verifiers still built index-based paths,
    found nothing, and reported good audio as unverifiable."""

    def _helper(self):
        import importlib.util
        from pathlib import Path
        path = (
            Path(__file__).resolve().parents[1]
            / ".claude" / "skills" / "narrate-texts" / "scripts" / "chunk_paths.py"
        )
        spec = importlib.util.spec_from_file_location("_chunk_paths", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_finds_a_content_keyed_chunk(self, runner, tmp_path):
        helper = self._helper()
        chunks = tmp_path / ".chunks"
        chunks.mkdir()
        texts = ["Eka virke.", "Toka virke."]
        keys = runner._chunk_keys(texts, "fi", "")
        for key in keys:
            runner._chunk_cache_path(chunks, key).write_bytes(b"x")
        found = helper.chunk_wav_path(tmp_path, 1, texts, "fi")
        assert found.exists()
        assert found.name.startswith("chunk_")

    def test_lists_both_naming_schemes(self, runner, tmp_path):
        helper = self._helper()
        chunks = tmp_path / ".chunks"
        chunks.mkdir()
        (chunks / "ch01_chunk0000.wav").write_bytes(b"old")
        runner._chunk_cache_path(
            chunks, runner._chunk_keys(["Eka."], "fi", "")[0]
        ).write_bytes(b"new")
        assert len(helper.chunk_wavs(tmp_path)) == 2

    def test_falls_back_to_the_old_name(self, tmp_path):
        """A cache written by an older build must still be verifiable."""
        helper = self._helper()
        chunks = tmp_path / ".chunks"
        chunks.mkdir()
        (chunks / "ch01_chunk0000.wav").write_bytes(b"old")
        assert helper.chunk_wav_path(tmp_path, 0, ["Eka."], "fi").name == \
            "ch01_chunk0000.wav"
