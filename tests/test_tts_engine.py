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
    _roman_to_int,
    _expand_acronyms,
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


# ---------------------------------------------------------------------------
# Pass K — Finnish abbreviation expansion
# ---------------------------------------------------------------------------


class TestAbbreviationExpansion:
    def test_esim_expanded(self) -> None:
        result = normalize_finnish_text("Esim. tämä")
        assert "esimerkiksi" in result
        assert "esim." not in result.lower()

    def test_mm_with_period_is_muun_muassa(self) -> None:
        result = normalize_finnish_text("Tämä on mm. hyvä.")
        assert "muun muassa" in result
        assert "millimetriä" not in result

    def test_jne(self) -> None:
        result = normalize_finnish_text("ja niin edelleen jne.")
        assert "ja niin edelleen" in result

    def test_yms(self) -> None:
        result = normalize_finnish_text("koirat, kissat yms.")
        assert "ynnä muuta sellaista" in result

    def test_eaa(self) -> None:
        result = normalize_finnish_text("vuonna 500 eaa.")
        assert "ennen ajanlaskun alkua" in result

    def test_ekr(self) -> None:
        result = normalize_finnish_text("syntyi 100 eKr.")
        assert "ennen Kristusta" in result

    def test_tri_before_capital_name(self) -> None:
        result = normalize_finnish_text("tri Virtanen tuli")
        assert "tohtori Virtanen" in result
        assert "tri Virtanen" not in result

    def test_tri_not_expanded_without_capital_name(self) -> None:
        # `tri` alone (followed by lowercase) must NOT be expanded
        result = normalize_finnish_text("tri on lyhenne")
        assert "tri" in result
        assert "tohtori" not in result

    def test_case_insensitive_match(self) -> None:
        for variant in ("Ts. tämä on", "TS. tämä on", "ts. tämä on"):
            result = normalize_finnish_text(variant)
            assert "toisin sanoen" in result, f"Failed for: {variant!r}"

    def test_prof_expanded(self) -> None:
        result = normalize_finnish_text("prof. Mäkinen puhui")
        assert "professori" in result
        assert "prof." not in result

    def test_dos_expanded(self) -> None:
        result = normalize_finnish_text("dos. Korhonen kirjoitti")
        assert "dosentti" in result
        assert "dos." not in result

    def test_abbrev_does_not_eat_random_word_ending_in_same_letters(self) -> None:
        # "nero" should not be treated as if it starts with "ns." or "nk."
        result = normalize_finnish_text("nero on lahjakas")
        assert "nero" in result


# ---------------------------------------------------------------------------
# Pass M — measurement unit / currency symbol expansion
# ---------------------------------------------------------------------------


class TestUnitSymbolExpansion:
    def test_percent_with_space(self) -> None:
        result = normalize_finnish_text("5 %")
        assert "viisi prosenttia" in result

    def test_percent_without_space(self) -> None:
        result = normalize_finnish_text("5%")
        assert "viisi prosenttia" in result

    def test_per_mille(self) -> None:
        result = normalize_finnish_text("3 \u2030")
        assert "kolme promillea" in result

    def test_euros(self) -> None:
        result = normalize_finnish_text("20 \u20ac")
        assert "kaksikymmentä euroa" in result

    def test_dollars_prefix(self) -> None:
        result = normalize_finnish_text("$5")
        assert "viisi dollaria" in result

    def test_kilometers(self) -> None:
        result = normalize_finnish_text("3 km")
        assert "kolme kilometriä" in result

    def test_centimeters(self) -> None:
        result = normalize_finnish_text("15 cm")
        assert "viisitoista senttimetriä" in result

    def test_millimeters_unit_not_abbrev(self) -> None:
        # `5 mm` (digit + mm, no period) must expand as unit, not abbreviation
        result = normalize_finnish_text("5 mm")
        assert "viisi millimetriä" in result
        assert "muun muassa" not in result

    def test_kilograms(self) -> None:
        result = normalize_finnish_text("2 kg")
        assert "kaksi kilogrammaa" in result

    def test_temperature_celsius_positive(self) -> None:
        result = normalize_finnish_text("20 \u00b0C")
        assert "kaksikymmentä celsiusastetta" in result

    def test_temperature_celsius_negative(self) -> None:
        # Negative temperature: -5 °C — the number part is negative
        result = normalize_finnish_text("-5 \u00b0C")
        assert "celsiusastetta" in result

    def test_minutes(self) -> None:
        result = normalize_finnish_text("5 min")
        assert "viisi minuuttia" in result

    def test_unit_does_not_match_without_digit_prefix(self) -> None:
        result = normalize_finnish_text("kilometrin matka")
        assert "kilometrin" in result
        # Should not be expanded (no digit prefix)
        assert "kilometriä" not in result

    def test_mm_alone_without_digit_is_not_a_unit(self) -> None:
        # `mm.` with a period is the abbreviation "muun muassa", not a unit
        result = normalize_finnish_text("mm. on hyvä")
        assert "muun muassa" in result
        assert "millimetriä" not in result

    def test_cm_before_km_disambiguation(self) -> None:
        result = normalize_finnish_text("5 cm ja 10 km")
        assert "viisi senttimetriä" in result
        assert "kymmenen kilometriä" in result


# ---------------------------------------------------------------------------
# Pass D (range polish) — per-endpoint governor inflection via Pass G
# ---------------------------------------------------------------------------


class TestRangePolish:
    def test_year_range_radio_default_both_nominative(self) -> None:
        # Default radio mode: year governor overrides case to nominative
        result = normalize_finnish_text("vuosina 1914-1918")
        # Both endpoints contain tuhat yhdeksänsataa (nominative prefix)
        assert result.count("tuhat yhdeksänsataa") >= 2
        # Essive form must NOT appear
        assert "tuhantena" not in result

    def test_year_range_full_mode_both_inflect(self) -> None:
        result = normalize_finnish_text(
            "vuosina 1914-1918", year_shortening="full"
        )
        # Essive form must appear for both endpoints
        assert result.count("tuhantena") >= 2

    def test_range_with_vuodesta_governor_full_mode(self) -> None:
        result = normalize_finnish_text(
            "vuodesta 1917-1920", year_shortening="full"
        )
        # Both endpoints should have the elative form
        assert result.count("tuhannesta") >= 2

    def test_range_with_no_governor_falls_back_to_nominative(self) -> None:
        result = normalize_finnish_text("1500-1800")
        # Both endpoints in nominative (no governor)
        assert "tuhat viisisataa" in result
        assert "tuhat kahdeksansataa" in result


# ---------------------------------------------------------------------------
# Pass L — Roman numeral expansion
# ---------------------------------------------------------------------------


class TestRomanNumeralExpansion:
    # -- Regnal ordinals -------------------------------------------------------

    def test_kustaa_ii_aadolf(self) -> None:
        result = normalize_finnish_text("Kustaa II Aadolf oli kuningas")
        assert "Kustaa toinen Aadolf oli kuningas" == result

    def test_pius_ix(self) -> None:
        result = normalize_finnish_text("paavi Pius IX")
        assert "yhdeksäs" in result
        assert "IX" not in result

    def test_leo_xiii(self) -> None:
        result = normalize_finnish_text("Leo XIII")
        assert "kolmastoista" in result
        assert "XIII" not in result

    def test_katariina_ii(self) -> None:
        result = normalize_finnish_text("Katariina II")
        assert "toinen" in result
        assert "II" not in result

    def test_henrik_viii(self) -> None:
        result = normalize_finnish_text("Henrik VIII")
        assert "kahdeksas" in result
        assert "VIII" not in result

    def test_kuningas_juhana_iii(self) -> None:
        result = normalize_finnish_text("kuningas Juhana III")
        assert "kolmas" in result
        assert "III" not in result

    def test_tsaari_nikolai_ii(self) -> None:
        result = normalize_finnish_text("tsaari Nikolai II")
        assert "toinen" in result
        assert "II" not in result

    # -- Chapter / century ordinals -------------------------------------------

    def test_luku_iv(self) -> None:
        result = normalize_finnish_text("luku IV käsittelee")
        assert "neljäs" in result
        assert "IV" not in result

    def test_xix_vuosisata(self) -> None:
        result = normalize_finnish_text("XIX vuosisata")
        assert "yhdeksästoista" in result
        assert "XIX" not in result

    def test_xx_luvulla(self) -> None:
        result = normalize_finnish_text("XX luvulla")
        assert "kahdeskymmenes" in result
        assert "XX" not in result

    def test_pykala_xii(self) -> None:
        result = normalize_finnish_text("pykälä XII")
        assert "kahdestoista" in result
        assert "XII" not in result

    # -- Cardinal fallback -----------------------------------------------------

    def test_ii_alone_no_context(self) -> None:
        result = normalize_finnish_text("II oli aikakausi")
        assert "kaksi" in result
        assert "II" not in result

    def test_iv_with_unknown_preceding_word(self) -> None:
        result = normalize_finnish_text("Talo IV")
        # cardinal fallback — no regnal/title/section context
        assert "neljä" in result
        assert "IV" not in result

    def test_mcm_year(self) -> None:
        # MCM = 1900, no context → cardinal
        result = normalize_finnish_text("MCM")
        assert "tuhat yhdeksänsataa" in result
        assert "MCM" not in result

    # -- Blacklist checks ------------------------------------------------------

    def test_dc_not_expanded(self) -> None:
        result = normalize_finnish_text("DC power")
        assert "DC" in result

    def test_cv_not_expanded(self) -> None:
        result = normalize_finnish_text("lähetä CV")
        assert "CV" in result

    def test_mvp_not_expanded(self) -> None:
        result = normalize_finnish_text("tämän kauden MVP")
        assert "MVP" in result

    def test_lcd_not_expanded(self) -> None:
        result = normalize_finnish_text("LCD näyttö")
        assert "LCD" in result

    # -- Single-letter guard ---------------------------------------------------

    def test_standalone_i_not_expanded(self) -> None:
        result = normalize_finnish_text("I said no")
        # Single I must not be expanded (regex requires 2+ chars)
        assert "I" in result

    def test_standalone_v_not_expanded(self) -> None:
        # Single V should not be touched
        result = normalize_finnish_text("Olen paikalla. V kertaa.")
        assert "V " in result or "V." in result

    def test_standalone_x_not_expanded(self) -> None:
        result = normalize_finnish_text("X marks the spot")
        assert "X" in result

    # -- Edge cases ------------------------------------------------------------

    def test_invalid_roman_stays_unchanged(self) -> None:
        # IIII is non-canonical (canonical is IV); _roman_to_int returns None.
        # The regex matches IIII but the round-trip canonicity check rejects it,
        # so the token is left unchanged.
        assert _roman_to_int("IIII") is None
        result = normalize_finnish_text("IIII")
        assert "IIII" in result

    def test_roman_at_sentence_start(self) -> None:
        result = normalize_finnish_text("IX vuosisadalla")
        assert "yhdeksäs" in result
        assert "IX" not in result

    def test_multiple_romans_in_one_sentence(self) -> None:
        result = normalize_finnish_text("Kustaa II ja Juhana III")
        assert "toinen" in result
        assert "kolmas" in result
        assert "II" not in result
        assert "III" not in result

    # -- _roman_to_int unit tests ----------------------------------------------

    def test_roman_to_int_basic_values(self) -> None:
        assert _roman_to_int("IV") == 4
        assert _roman_to_int("IX") == 9
        assert _roman_to_int("XIV") == 14
        assert _roman_to_int("MCM") == 1900

    def test_roman_to_int_invalid_returns_none(self) -> None:
        assert _roman_to_int("IIII") is None
        assert _roman_to_int("VV") is None
        assert _roman_to_int("") is None

# ---------------------------------------------------------------------------
# Pass A extension — metadata paren drop
# ---------------------------------------------------------------------------


class TestMetadataParenDrop:
    def test_isbn_paren_dropped(self) -> None:
        result = normalize_finnish_text("(ISBN 978-951-123-456-7)", drop_citations=True)
        assert "ISBN" not in result
        assert "978" not in result

    def test_doi_paren_dropped(self) -> None:
        result = normalize_finnish_text("(DOI 10.1234/abcd)", drop_citations=True)
        assert "DOI" not in result
        assert "10.1234" not in result

    def test_creative_commons_paren_dropped(self) -> None:
        result = normalize_finnish_text(
            "(Creative Commons Nimeä 4.0 Kansainvälinen)", drop_citations=True
        )
        assert "Creative Commons" not in result

    def test_cc_by_dropped(self) -> None:
        result = normalize_finnish_text("(CC BY 4.0)", drop_citations=True)
        assert "CC BY" not in result

    def test_nonmetadata_paren_untouched(self) -> None:
        result = normalize_finnish_text("(tämä on huomautus)", drop_citations=True)
        assert "tämä on huomautus" in result

    def test_metadata_paren_kept_when_drop_citations_false(self) -> None:
        result = normalize_finnish_text("(DOI 10.1234/abcd)", drop_citations=False)
        assert "DOI" in result


# ---------------------------------------------------------------------------
# Pass J1 — ellipsis collapse
# ---------------------------------------------------------------------------


class TestEllipsisCollapse:
    def test_three_dots_surrounded_by_space(self) -> None:
        result = normalize_finnish_text("Hmm ... hän sanoi")
        assert "…" in result
        assert "..." not in result

    def test_four_dots_surrounded_by_space(self) -> None:
        result = normalize_finnish_text("Odottakaa .... valmis")
        assert "…" in result
        assert "...." not in result

    def test_decimal_not_collapsed(self) -> None:
        # Decimals like 1.5 should not be affected — Pass F handles those
        result = normalize_finnish_text("1.5 prosenttia")
        assert "…" not in result

    def test_url_dots_not_collapsed(self) -> None:
        # Dots inside a word (no surrounding whitespace) must not collapse
        result = normalize_finnish_text("example.com on osoite")
        assert "…" not in result


# ---------------------------------------------------------------------------
# Pass J2 — TOC dot-leader drop
# ---------------------------------------------------------------------------


class TestTocDotLeaderDrop:
    def test_toc_line_dropped(self) -> None:
        result = normalize_finnish_text("RAJAT..............42")
        assert "RAJAT" in result
        assert "42" not in result
        # No long dot run left
        assert "......." not in result

    def test_lowercase_toc(self) -> None:
        result = normalize_finnish_text("Johdanto.........1")
        assert "Johdanto" in result
        assert "........." not in result

    def test_toc_with_leading_spaces(self) -> None:
        result = normalize_finnish_text("   Luku 1 .............. 5")
        assert "Luku" in result
        assert ".............." not in result

    def test_real_ellipsis_preserved(self) -> None:
        # Only 3 dots with no digit after — Pass J2 must not touch this;
        # Pass J1 converts it to Unicode ellipsis
        result = normalize_finnish_text("Hmm... valmis")
        # Pass J1 did not fire (no surrounding whitespace), so the dots survive
        # as-is OR Pass J1 fires — either way no Pass J2 damage
        assert "valmis" in result

    def test_five_dots_followed_by_digit(self) -> None:
        result = normalize_finnish_text("RAJAT..... 42")
        assert "RAJAT" in result
        assert "....." not in result


# ---------------------------------------------------------------------------
# Pass J3 — ISBN strip
# ---------------------------------------------------------------------------


class TestIsbnStrip:
    def test_isbn_13_with_hyphens(self) -> None:
        result = normalize_finnish_text("Kirja ISBN 978-951-123-456-7 on hyvä")
        assert "ISBN" not in result
        assert "978" not in result
        assert "Kirja" in result
        assert "hyvä" in result

    def test_isbn_13_without_prefix(self) -> None:
        result = normalize_finnish_text("9789511234567 on kirja")
        assert "9789511234567" not in result
        assert "kirja" in result

    def test_isbn_13_with_spaces(self) -> None:
        result = normalize_finnish_text("ISBN 978 951 123 456 7")
        assert "ISBN" not in result
        assert "978" not in result

    def test_isbn_in_sentence(self) -> None:
        # The metadata-paren drop handles parens; ISBN strip handles bare numbers
        result = normalize_finnish_text("(ISBN: 978-951-123-456-7)", drop_citations=True)
        assert "978" not in result

    def test_non_isbn_digits_preserved(self) -> None:
        result = normalize_finnish_text("Vuonna 1918 tapahtui")
        assert "1918" not in result  # Pass G expands it to words
        # But the year word form should be present
        assert "yhdeksäntoista" in result or "tuhat" in result
# TestAcronymExpansion
# ---------------------------------------------------------------------------


class TestAcronymExpansion:
    """Tests for _expand_acronyms (Pass N) and its integration via normalize_finnish_text."""

    # --- Positive expansion ---

    def test_eu_expanded(self) -> None:
        assert _expand_acronyms("EU on liitto") == "Euroopan unioni on liitto"

    def test_yk_expanded(self) -> None:
        assert _expand_acronyms("YK päätti") == "Yhdistyneet kansakunnat päätti"

    def test_usa_expanded(self) -> None:
        assert _expand_acronyms("USA oli") == "Yhdysvallat oli"

    def test_nato_expanded(self) -> None:
        # NATO reads as a word, not letter-by-letter
        assert _expand_acronyms("NATO jäsenyys") == "Nato jäsenyys"

    def test_alr_letter_by_letter(self) -> None:
        assert _expand_acronyms("ALR sääti") == "A L R sääti"

    def test_abgb_letter_by_letter(self) -> None:
        # Hyphen is a non-word char so \b fires between ABGB and -; ABGB IS expanded.
        assert _expand_acronyms("ABGB-laki") == "A B G B-laki"

    def test_bgb_letter_by_letter(self) -> None:
        assert _expand_acronyms("BGB § 242") == "B G B § 242"

    def test_hgb_letter_by_letter(self) -> None:
        assert _expand_acronyms("HGB") == "H G B"

    def test_multiple_acronyms(self) -> None:
        assert _expand_acronyms("EU ja YK") == "Euroopan unioni ja Yhdistyneet kansakunnat"

    # --- Negative (don't expand) ---

    def test_lowercase_not_expanded(self) -> None:
        # `eu` is a Finnish negative prefix and must NOT be expanded
        original = "eu on suomen kielessä tavu"
        assert _expand_acronyms(original) == original

    def test_unknown_acronym_untouched(self) -> None:
        original = "XYZ on akronyymi"
        assert _expand_acronyms(original) == original

    def test_partial_match_untouched(self) -> None:
        # `NATOn` is one word token — no \b inside it; NATO is NOT matched.
        # This is by design: we only expand exact standalone tokens.
        original = "NATOn jäsenyys"
        assert _expand_acronyms(original) == original

    # --- Word-boundary edge cases ---

    def test_acronym_at_sentence_start(self) -> None:
        assert _expand_acronyms("EU päätti.") == "Euroopan unioni päätti."

    def test_acronym_at_sentence_end(self) -> None:
        assert _expand_acronyms("jäsenyys EU.") == "jäsenyys Euroopan unioni."

    def test_acronym_with_colon_suffix(self) -> None:
        # Finnish inflection uses colon: `EU:n`. The colon is a non-word char
        # so \b fires between `EU` and `:` — EU IS matched and expanded.
        # `YK:n` likewise.
        assert _expand_acronyms("EU:n") == "Euroopan unioni:n"
        assert _expand_acronyms("YK:n") == "Yhdistyneet kansakunnat:n"

    # --- Longest-first disambiguation ---

    def test_abgb_matches_before_bgb(self) -> None:
        # Ensures `ABGB` is NOT partially replaced as `A` + `BGB` expansion.
        result = _expand_acronyms("ABGB ja BGB")
        assert result == "A B G B ja B G B"

    # --- Integration: normalize_finnish_text passes through Pass N ---

    def test_eu_expanded_via_normalize(self) -> None:
        result = normalize_finnish_text("EU on liitto")
        assert "Euroopan unioni" in result


# ---------------------------------------------------------------------------
# Session 4 polish — § expansion, Pass H morpheme ordering, Pass D short ranges
# ---------------------------------------------------------------------------


class TestSectionSignExpansion:
    """Pass M now recognizes `§` as a prefix symbol that expands to
    `pykälä`. Subsequent digits are inflected by Pass G via the
    `pykälä` before-governor (nominative default)."""

    def test_section_sign_with_space(self) -> None:
        result = normalize_finnish_text("§ 5")
        assert "pykälä" in result
        assert "viisi" in result
        assert "§" not in result

    def test_section_sign_without_space(self) -> None:
        result = normalize_finnish_text("§5 on laki")
        assert "pykälä viisi" in result
        assert "§" not in result

    def test_section_sign_with_larger_number(self) -> None:
        # Regression check: § 242 must not trigger the Pass H ordering bug
        result = normalize_finnish_text("§ 242")
        assert "pykälä" in result
        assert "kaksisataa neljäkymmentä kaksi" in result
        assert "sataan" not in result.split()[1:]  # no false illative split


class TestPassHMorphemeOrdering:
    """Pass H's morpheme splitter must prefer partitive `sataa` /
    `kymmentä` over illative `sataan` / `kymmeneen` inside compound
    numbers so it doesn't steal a leading letter from the next
    morpheme. See the explicit-ordering list in `_FI_MORPHEME_STEMS`."""

    def test_242_nominative_splits_correctly(self) -> None:
        result = normalize_finnish_text("242")
        # Must be `kaksisataa neljäkymmentä kaksi`
        # NOT `kaksisataan eljäkymmentä kaksi` (the old bug).
        assert "kaksisataa neljäkymmentä kaksi" in result
        # The bad form has a standalone `eljäkymmentä` preceded by a
        # space (the orphaned first letter after the false split).
        assert " eljäkymmentä" not in result
        assert "kaksisataan " not in result

    def test_345_nominative_splits_correctly(self) -> None:
        result = normalize_finnish_text("345")
        assert "kolmesataa neljäkymmentä viisi" in result
        assert "sataan" not in result

    def test_1889_still_splits_correctly(self) -> None:
        # Regression from session 1.
        result = normalize_finnish_text("1889")
        assert "kahdeksansataa kahdeksankymmentä yhdeksän" in result


class TestShortRangeGovernorInflection:
    """Pass D now matches 1–4 digit ranges so short ranges like
    `sivuilta 42–45` also travel through Pass G's governor detection
    and both endpoints inflect correctly."""

    def test_sivuilta_plural_ablative_range(self) -> None:
        # `sivuilta 42-45` → both endpoints in ablative
        result = normalize_finnish_text("sivuilta 42-45")
        # Ablative forms of 42 and 45 both appear
        assert "neljältäkymmeneltä kahdelta" in result
        assert "neljältäkymmeneltä viideltä" in result

    def test_sivulta_singular_ablative_short_range(self) -> None:
        result = normalize_finnish_text("sivulta 3-5")
        assert "kolmelta" in result
        assert "viideltä" in result

    def test_riveilta_range(self) -> None:
        result = normalize_finnish_text("riveiltä 10-12")
        assert "kymmeneltä" in result
        assert "kahdeltatoista" in result

    def test_bare_short_range_nominative_fallback(self) -> None:
        # No governor → both endpoints in nominative (acceptable)
        result = normalize_finnish_text("5-2")
        assert "viisi" in result
        assert "kaksi" in result

    def test_year_range_still_works(self) -> None:
        # Regression check — long year ranges continue to work.
        result = normalize_finnish_text("vuonna 1500-1800")
        assert "tuhat viisisataa" in result
        assert "tuhat kahdeksansataa" in result
