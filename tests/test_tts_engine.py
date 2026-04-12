"""Unit tests for tts_engine module.

Network calls to edge-tts are mocked. Only local logic is tested here.
Integration tests (actual synthesis) require internet access and are skipped in CI.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip all tests that require ffmpeg if it is not installed
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
requires_ffmpeg = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg not installed"
)

from src.tts_engine import (
    TTSConfig,
    VOICES,
    combine_audio_files,
    chapters_to_speech,
    normalize_finnish_text,
    split_text_into_chunks,
    text_to_speech,
    _force_split,
    _split_sentences,
    MAX_CHUNK_CHARS,
)


# ---------------------------------------------------------------------------
# TTSConfig
# ---------------------------------------------------------------------------


class TestTTSConfig:
    def test_default_language_is_finnish(self) -> None:
        cfg = TTSConfig()
        assert cfg.language == "fi"

    def test_resolved_voice_uses_default_for_language(self) -> None:
        cfg = TTSConfig(language="fi")
        assert cfg.resolved_voice() == VOICES["fi"]["default"]

    def test_resolved_voice_respects_explicit_voice(self) -> None:
        cfg = TTSConfig(voice="en-US-GuyNeural")
        assert cfg.resolved_voice() == "en-US-GuyNeural"

    def test_unknown_language_falls_back_to_finnish(self) -> None:
        cfg = TTSConfig(language="xx")
        assert cfg.resolved_voice() == VOICES["fi"]["default"]

    def test_tts_config_normalize_default_is_true(self) -> None:
        cfg = TTSConfig()
        assert cfg.normalize_text is True

    def test_tts_config_normalize_can_be_disabled(self) -> None:
        cfg = TTSConfig(normalize_text=False)
        assert cfg.normalize_text is False


# ---------------------------------------------------------------------------
# normalize_finnish_text
# ---------------------------------------------------------------------------


class TestNormalizeFinnishText:
    def test_normalize_finnish_text_expands_year_numbers(self) -> None:
        result = normalize_finnish_text("vuonna 1500")
        assert not any(ch.isdigit() for ch in result)
        assert "vuonna" in result

    def test_normalize_finnish_text_handles_century_expressions(self) -> None:
        result = normalize_finnish_text("1500-luvulla")
        assert "luvulla" in result
        assert not any(ch.isdigit() for ch in result)

    def test_normalize_finnish_text_english_passes_through(self) -> None:
        # Finnish-only normalizer: plain English without digits is unchanged.
        text = "This is an English sentence."
        assert normalize_finnish_text(text) == text

    def test_normalize_finnish_text_empty_string(self) -> None:
        assert normalize_finnish_text("") == ""

    def test_normalize_finnish_text_drops_citations(self) -> None:
        result = normalize_finnish_text(
            "Tämä on väite (Pihlajamäki 2005).", drop_citations=True
        )
        assert "Pihlajamäki" not in result
        assert "2005" not in result

    def test_normalize_finnish_text_keeps_citations_when_disabled(self) -> None:
        result = normalize_finnish_text(
            "Tämä on väite (Pihlajamäki 2005).", drop_citations=False
        )
        assert "Pihlajamäki" in result

    def test_compound_numbers_get_spaces(self) -> None:
        # num2words 0.5.14 glues Finnish compound-number morphemes
        # together (e.g. 1889 -> "kahdeksansataakahdeksankymmentäyhdeksän").
        # Post-processor must insert spaces at morpheme boundaries so
        # Chatterbox-TTS tokenizes and pronounces them correctly.
        out = normalize_finnish_text("1889")
        assert " " in out
        assert "kahdeksansataa kahdeksankymmentä" in out
        assert "kahdeksankymmentä yhdeksän" in out
        assert "kahdeksansataakahdeksankymmentäyhdeksän" not in out

        # Two-digit compound: 42 -> "neljäkymmentä kaksi".
        assert normalize_finnish_text("42") == "neljäkymmentä kaksi"

        # Hundreds + teens: 1917 -> spaces between sataa and seitsemäntoista.
        out_1917 = normalize_finnish_text("1917")
        assert "yhdeksänsataa seitsemäntoista" in out_1917

        # Standalone teens must NOT be split — they are single word units.
        assert normalize_finnish_text("15") == "viisitoista"
        assert normalize_finnish_text("11") == "yksitoista"

        # Clean hundreds (no trailing morpheme) must not get a spurious space.
        assert normalize_finnish_text("1500") == "tuhat viisisataa"


# ---------------------------------------------------------------------------
# Governor-word case detection (Pass G)
# ---------------------------------------------------------------------------


class TestFinnishGovernorCases:
    """Numerals must inflect to agree with their governor word.

    The reference for every expected form is
    ``docs/finnish_governor_cases.md`` (VISK §772 + Kielikello).
    """

    def test_sivulta_takes_ablative(self) -> None:
        # "sivulta 42" → ablative "neljältäkymmeneltäkahdelta"
        out = normalize_finnish_text("sivulta 42")
        assert "neljältäkymmeneltä" in out
        assert "kahdelta" in out
        assert "42" not in out

    def test_sivulla_takes_adessive(self) -> None:
        out = normalize_finnish_text("sivulla 42")
        assert "neljälläkymmenellä" in out
        assert "kahdella" in out

    def test_sivulle_takes_allative(self) -> None:
        out = normalize_finnish_text("sivulle 42")
        assert "neljällekymmenelle" in out
        assert "kahdelle" in out

    def test_luvussa_takes_inessive(self) -> None:
        # "luvussa 3" → inessive "kolmessa"
        out = normalize_finnish_text("luvussa 3")
        assert "kolmessa" in out

    def test_kappaleessa_takes_inessive(self) -> None:
        out = normalize_finnish_text("kappaleessa 7")
        assert "seitsemässä" in out

    def test_kohdassa_takes_inessive(self) -> None:
        out = normalize_finnish_text("kohdassa 4")
        assert "neljässä" in out

    def test_rivilla_takes_adessive(self) -> None:
        out = normalize_finnish_text("rivillä 12")
        assert "kahdellatoista" in out

    def test_klo_stays_nominative(self) -> None:
        # "klo 14" — kello is a frozen adverbial, hour stays nominative.
        # Expected reading: "kello neljätoista" (clock fourteen).
        out = normalize_finnish_text("klo 14")
        assert "neljätoista" in out
        assert "neljässätoista" not in out

    def test_kello_stays_nominative(self) -> None:
        out = normalize_finnish_text("kello 8")
        assert "kahdeksan" in out
        assert "kahdeksalla" not in out

    def test_viisi_kertaa_numeral_stays_nominative(self) -> None:
        # VISK §772: "X kertaa" → number in nominative, kertaa keeps
        # its (frozen) partitive.
        out = normalize_finnish_text("5 kertaa")
        assert out == "viisi kertaa"

    def test_kolme_prosenttia_numeral_stays_nominative(self) -> None:
        out = normalize_finnish_text("3 prosenttia")
        assert out == "kolme prosenttia"

    def test_vuotta_numeral_stays_nominative(self) -> None:
        out = normalize_finnish_text("10 vuotta")
        assert "kymmenen vuotta" in out

    def test_bare_integer_with_no_governor_is_nominative(self) -> None:
        # Regression: unchanged behaviour for unmarked bare ints.
        assert normalize_finnish_text("42") == "neljäkymmentä kaksi"
        assert normalize_finnish_text("15") == "viisitoista"

    def test_governor_match_is_case_insensitive(self) -> None:
        # "Sivulta 42" at sentence start must still detect the governor.
        out = normalize_finnish_text("Sivulta 42")
        assert "neljältäkymmeneltä" in out

    def test_governor_scan_window_is_three_words(self) -> None:
        # Governor exactly 3 word tokens before the number — must hit.
        out = normalize_finnish_text("sivulta tämän erittäin 42")
        assert "neljältäkymmeneltä" in out

    def test_governor_beyond_three_words_is_ignored(self) -> None:
        # Governor 4 words before the number — must NOT hit; fall
        # back to nominative.
        out = normalize_finnish_text("sivulta yksi kaksi kolme neljä 42")
        assert "neljäkymmentä kaksi" in out
        assert "neljältäkymmeneltä" not in out


# ---------------------------------------------------------------------------
# year_shortening flag (Kielikello radio convention)
# ---------------------------------------------------------------------------


class TestYearShortening:
    """The ``year_shortening`` kwarg chooses between radio and full case."""

    def test_radio_default_keeps_year_nominative(self) -> None:
        # vuodesta 1917 → "vuodesta tuhat yhdeksänsataa seitsemäntoista"
        out = normalize_finnish_text("vuodesta 1917 alkaen")
        assert "tuhat" in out
        assert "yhdeksänsataa" in out
        assert "seitsemäntoista" in out
        # Must NOT contain the elative form of the year.
        assert "tuhannesta" not in out

    def test_full_mode_emits_elative(self) -> None:
        out = normalize_finnish_text(
            "vuodesta 1917 alkaen", year_shortening="full"
        )
        assert "tuhannesta" in out
        assert "seitsemästätoista" in out

    def test_full_mode_emits_illative_for_vuoteen(self) -> None:
        out = normalize_finnish_text(
            "vuoteen 1900 mennessä", year_shortening="full"
        )
        assert "tuhanteen" in out

    def test_radio_mode_ignores_vuoteen(self) -> None:
        out = normalize_finnish_text("vuoteen 1900 mennessä")
        assert "tuhanteen" not in out
        assert "tuhat" in out

    def test_short_integer_with_year_governor_is_not_a_year(self) -> None:
        # "vuoden 5" — 5 is below the 1000 year threshold, so the
        # year-governor override does not apply. Since "vuoden" is
        # nominative in the governor table anyway, the numeral stays
        # nominative.
        out = normalize_finnish_text("vuoden 5 jälkeen")
        assert "viisi" in out


# ---------------------------------------------------------------------------
# Page abbreviation expansion (Pass E) leaves digits for Pass G
# ---------------------------------------------------------------------------


class TestPageAbbreviation:
    def test_s_abbrev_expands_to_sivu_and_inflects_via_governor(self) -> None:
        # "s. 42" → Pass E emits "sivu 42", then Pass G sees "sivu"
        # as a nominative governor and expands 42 in nominative.
        out = normalize_finnish_text("s. 42")
        assert "sivu" in out
        assert "neljäkymmentä kaksi" in out
        assert "s." not in out

    def test_ss_abbrev_expands_to_sivut(self) -> None:
        out = normalize_finnish_text("ss. 42")
        assert "sivut" in out


class TestTTSConfigYearShortening:
    def test_tts_config_year_shortening_default_is_radio(self) -> None:
        cfg = TTSConfig()
        assert cfg.year_shortening == "radio"

    def test_tts_config_year_shortening_can_be_full(self) -> None:
        cfg = TTSConfig(year_shortening="full")
        assert cfg.year_shortening == "full"


# ---------------------------------------------------------------------------
# split_text_into_chunks
# ---------------------------------------------------------------------------


class TestSplitTextIntoChunks:
    def test_empty_text_returns_empty_list(self) -> None:
        assert split_text_into_chunks("") == []
        assert split_text_into_chunks("   ") == []

    def test_short_text_is_single_chunk(self) -> None:
        text = "Lyhyt teksti."
        chunks = split_text_into_chunks(text, max_chars=500)
        assert len(chunks) == 1
        assert "Lyhyt teksti" in chunks[0]

    def test_chunks_do_not_exceed_max_chars(self) -> None:
        # Create text with many short sentences
        text = " ".join(["Lause numero " + str(i) + "." for i in range(200)])
        chunks = split_text_into_chunks(text, max_chars=200)
        for chunk in chunks:
            assert len(chunk) <= 200, f"Chunk too long: {len(chunk)}"

    def test_very_long_single_sentence_is_force_split(self) -> None:
        long_sentence = "sana " * 1000  # 5000 chars, no punctuation
        chunks = split_text_into_chunks(long_sentence, max_chars=300)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 300

    def test_all_text_preserved_across_chunks(self) -> None:
        sentences = ["Tämä on lause numero " + str(i) + "." for i in range(50)]
        text = " ".join(sentences)
        chunks = split_text_into_chunks(text, max_chars=300)
        combined = " ".join(chunks)
        # All original words should appear somewhere
        for i in range(50):
            assert str(i) in combined

    def test_no_empty_chunks(self) -> None:
        text = "A. B. C. D."
        chunks = split_text_into_chunks(text, max_chars=50)
        for chunk in chunks:
            assert chunk.strip() != ""


# ---------------------------------------------------------------------------
# _split_sentences — abbreviation and edge-case handling
# ---------------------------------------------------------------------------


class TestSplitSentences:
    def test_finnish_abbreviation_esim_does_not_split(self) -> None:
        text = "Tämä on esim. lause. Toinen lause."
        sentences = _split_sentences(text)
        assert len(sentences) == 2
        assert "esim." in sentences[0]

    def test_finnish_abbreviation_ks_does_not_split(self) -> None:
        text = "Ks. sivu 45. Seuraava lause alkaa."
        sentences = _split_sentences(text)
        assert len(sentences) == 2

    def test_initial_does_not_split(self) -> None:
        text = "H. Pihlajamäki kirjoitti tämän. Seuraava."
        sentences = _split_sentences(text)
        assert len(sentences) == 2
        assert "H. Pihlajamäki" in sentences[0]

    def test_decimal_number_does_not_split(self) -> None:
        text = "Arvo on 5.2 metriä. Toinen lause."
        sentences = _split_sentences(text)
        assert len(sentences) == 2

    def test_domain_name_does_not_split(self) -> None:
        text = "Katso google.com sivustoa. Seuraava."
        sentences = _split_sentences(text)
        assert len(sentences) == 2

    def test_real_sentence_end_splits(self) -> None:
        text = "Ensimmäinen lause. Toinen lause. Kolmas lause."
        sentences = _split_sentences(text)
        assert len(sentences) == 3

    def test_question_and_exclamation_split(self) -> None:
        text = "Kysymys? Vastaus! Toteamus."
        sentences = _split_sentences(text)
        assert len(sentences) == 3

    def test_english_abbreviations(self) -> None:
        text = "See Dr. Smith. He is a professor."
        sentences = _split_sentences(text)
        assert len(sentences) == 2
        assert "Dr. Smith" in sentences[0]


# ---------------------------------------------------------------------------
# _force_split
# ---------------------------------------------------------------------------


class TestForceSplit:
    def test_splits_on_word_boundaries(self) -> None:
        text = "yksi kaksi kolme neljä viisi"
        parts = _force_split(text, max_chars=12)
        assert all(len(p) <= 12 for p in parts)
        assert " ".join(parts)  # all words present

    def test_single_word_longer_than_max(self) -> None:
        # Can't split a single word — returns it as-is
        word = "a" * 500
        parts = _force_split(word, max_chars=100)
        assert len(parts) == 1
        assert parts[0] == word


# ---------------------------------------------------------------------------
# combine_audio_files
# ---------------------------------------------------------------------------


class TestCombineAudioFiles:
    def test_raises_on_empty_list(self) -> None:
        with pytest.raises(ValueError):
            combine_audio_files([], "/tmp/out.mp3")

    @requires_ffmpeg
    def test_combines_real_mp3s(self) -> None:
        """Create two minimal silent MP3s and combine them."""
        from pydub import AudioSegment

        with tempfile.TemporaryDirectory() as tmp:
            seg1 = AudioSegment.silent(duration=100)  # 100 ms
            seg2 = AudioSegment.silent(duration=100)
            f1 = os.path.join(tmp, "a.mp3")
            f2 = os.path.join(tmp, "b.mp3")
            seg1.export(f1, format="mp3")
            seg2.export(f2, format="mp3")

            out = os.path.join(tmp, "combined.mp3")
            combine_audio_files([f1, f2], out)

            assert os.path.exists(out)
            result = AudioSegment.from_mp3(out)
            assert len(result) >= 100


# ---------------------------------------------------------------------------
# text_to_speech (mocked)
# ---------------------------------------------------------------------------


def _make_fake_mp3(path: str) -> None:
    """Write a minimal valid MP3-like file using WAV wrapped content.

    Since ffmpeg is not available in the test environment we write a real WAV
    file but with an .mp3 extension and patch pydub to accept it.
    pydub.AudioSegment.from_mp3 actually just calls ffmpeg; to avoid that
    we patch combine_audio_files entirely in tests that need it.
    """
    from pydub import AudioSegment
    # Use WAV format which doesn't require ffmpeg
    seg = AudioSegment.silent(duration=50)
    seg.export(path, format="wav")


class TestTextToSpeech:
    def test_raises_on_empty_text(self) -> None:
        with pytest.raises(ValueError):
            text_to_speech("", "/tmp/out.mp3")

    @requires_ffmpeg
    def test_calls_progress_callback(self) -> None:
        from pydub import AudioSegment

        progress_calls: list[tuple] = []

        def cb(current: int, total: int, msg: str) -> None:
            progress_calls.append((current, total, msg))

        with patch("src.tts_engine._synthesize_chunk") as mock_synth:
            async def fake_synth(text, voice, rate, volume, output_path):
                seg = AudioSegment.silent(duration=50)
                seg.export(output_path, format="mp3")

            mock_synth.side_effect = fake_synth

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                out = f.name

            try:
                text_to_speech("Lyhyt teksti.", out, progress_cb=cb)
                assert len(progress_calls) >= 1
            finally:
                os.unlink(out)

    @requires_ffmpeg
    def test_text_to_speech_calls_normalizer_for_finnish(self) -> None:
        from pydub import AudioSegment

        with patch("src.tts_engine.normalize_finnish_text") as mock_norm, \
             patch("src.tts_engine._synthesize_chunk") as mock_synth:
            mock_norm.side_effect = lambda t, **kw: t + " NORMALIZED"

            async def fake_synth(text, voice, rate, volume, output_path):
                seg = AudioSegment.silent(duration=50)
                seg.export(output_path, format="mp3")

            mock_synth.side_effect = fake_synth

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                out = f.name

            try:
                text_to_speech(
                    "vuonna 1500",
                    out,
                    config=TTSConfig(language="fi", normalize_text=True),
                )
                assert mock_norm.called
                mock_norm.assert_called_with(
                    "vuonna 1500", year_shortening="radio"
                )
            finally:
                os.unlink(out)

    @requires_ffmpeg
    def test_text_to_speech_skips_normalizer_for_english(self) -> None:
        from pydub import AudioSegment

        with patch("src.tts_engine.normalize_finnish_text") as mock_norm, \
             patch("src.tts_engine._synthesize_chunk") as mock_synth:

            async def fake_synth(text, voice, rate, volume, output_path):
                seg = AudioSegment.silent(duration=50)
                seg.export(output_path, format="mp3")

            mock_synth.side_effect = fake_synth

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                out = f.name

            try:
                text_to_speech(
                    "In the year 1500",
                    out,
                    config=TTSConfig(language="en"),
                )
                assert not mock_norm.called
            finally:
                os.unlink(out)

    @requires_ffmpeg
    def test_text_to_speech_skips_normalizer_when_disabled(self) -> None:
        from pydub import AudioSegment

        with patch("src.tts_engine.normalize_finnish_text") as mock_norm, \
             patch("src.tts_engine._synthesize_chunk") as mock_synth:

            async def fake_synth(text, voice, rate, volume, output_path):
                seg = AudioSegment.silent(duration=50)
                seg.export(output_path, format="mp3")

            mock_synth.side_effect = fake_synth

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                out = f.name

            try:
                text_to_speech(
                    "vuonna 1500",
                    out,
                    config=TTSConfig(language="fi", normalize_text=False),
                )
                assert not mock_norm.called
            finally:
                os.unlink(out)

    @requires_ffmpeg
    def test_creates_output_file(self) -> None:
        from pydub import AudioSegment

        with patch("src.tts_engine._synthesize_chunk") as mock_synth:
            async def fake_synth(text, voice, rate, volume, output_path):
                seg = AudioSegment.silent(duration=50)
                seg.export(output_path, format="mp3")

            mock_synth.side_effect = fake_synth

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                out = f.name

            try:
                text_to_speech("Tämä on testi.", out)
                assert os.path.exists(out)
                assert os.path.getsize(out) > 0
            finally:
                os.unlink(out)
