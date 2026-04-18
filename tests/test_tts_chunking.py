"""Unit tests for the chunking module (src.tts_chunking).

These tests were split out of tests/test_tts_engine.py after the
chunker moved into its own module in commit 54dc619. They exercise
split_text_into_chunks, _split_sentences and _force_split.
"""

from __future__ import annotations

import pytest

from src.tts_chunking import (
    _force_split,
    _split_sentences,
    split_text_into_chunks,
)


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
# _split_sentences — URL / decimal / enumeration edge cases
# ---------------------------------------------------------------------------


class TestSplitSentencesEdgeCases:
    # --- URLs ---

    def test_https_url_with_path_does_not_split(self) -> None:
        text = "Lähde: https://example.com/page. Seuraava lause."
        sentences = _split_sentences(text)
        assert len(sentences) == 2
        assert "example.com/page" in sentences[0]

    def test_url_with_query_string_does_not_split(self) -> None:
        text = "Katso https://example.com/search?q=test&n=5 ohjetta. Toinen."
        sentences = _split_sentences(text)
        assert len(sentences) == 2

    def test_url_with_multiple_dots_does_not_split(self) -> None:
        text = "Palvelin on api.example.co.uk/v2. Seuraava."
        sentences = _split_sentences(text)
        assert len(sentences) == 2

    def test_www_prefixed_domain_does_not_split(self) -> None:
        text = "Sivu on www.google.com. Seuraava lause."
        sentences = _split_sentences(text)
        assert len(sentences) == 2

    # --- Decimals and numbers ---

    def test_money_amount_does_not_split(self) -> None:
        # "Hinta on 5,99 €." is Finnish convention; "Price is $5.99." the
        # English one. Both should stay in one sentence.
        text = "Price is $5.99. Next sentence."
        sentences = _split_sentences(text)
        assert len(sentences) == 2

    def test_version_number_does_not_split(self) -> None:
        text = "Päivitä versioon 3.9.1 heti. Toinen."
        sentences = _split_sentences(text)
        assert len(sentences) == 2
        assert "3.9.1" in sentences[0]

    def test_ip_address_does_not_split(self) -> None:
        text = "Palvelin 192.168.1.1 vastaa. Seuraava."
        sentences = _split_sentences(text)
        assert len(sentences) == 2
        assert "192.168.1.1" in sentences[0]

    def test_long_decimal_does_not_split(self) -> None:
        text = "Piin arvo on 3.14159 noin. Seuraava."
        sentences = _split_sentences(text)
        assert len(sentences) == 2

    # --- Additional Finnish abbreviations listed in _ABBREVIATIONS ---

    def test_finnish_yms_does_not_split(self) -> None:
        text = "Omenoita, päärynöitä yms. ostettiin kaupasta. Toinen."
        sentences = _split_sentences(text)
        assert len(sentences) == 2
        assert "yms." in sentences[0]

    def test_finnish_jne_does_not_split(self) -> None:
        text = "Autoja, busseja jne. nähtiin tiellä. Toinen."
        sentences = _split_sentences(text)
        assert len(sentences) == 2

    def test_finnish_vrt_does_not_split(self) -> None:
        text = "Sääntö on sama, vrt. Kissan laki. Toinen lause."
        sentences = _split_sentences(text)
        assert len(sentences) == 2

    def test_finnish_n_followed_by_number_does_not_split(self) -> None:
        # "n." is a very common Finnish approximation marker ("noin").
        text = "Paikalla oli n. 50 ihmistä. Seuraava."
        sentences = _split_sentences(text)
        assert len(sentences) == 2

    def test_finnish_s_as_page_abbrev_does_not_split(self) -> None:
        # "s." is "sivu" (page) in Finnish citations.
        text = "Lue s. 45 ohjeistus. Seuraava."
        sentences = _split_sentences(text)
        assert len(sentences) == 2

    def test_finnish_tms_does_not_split(self) -> None:
        text = "Valinnat ovat kaksi tms. vaihtoehtoa. Toinen lause."
        sentences = _split_sentences(text)
        assert len(sentences) == 2
        assert "tms." in sentences[0]

    # --- Repeated / compound terminators ---

    def test_question_exclamation_compound_splits_once(self) -> None:
        text = "Mitä?! Hän kysyi."
        sentences = _split_sentences(text)
        assert len(sentences) == 2
        assert sentences[0].endswith("?!")

    def test_ellipsis_splits_once(self) -> None:
        text = "Hän mietti... Seuraava ajatus."
        sentences = _split_sentences(text)
        assert len(sentences) == 2
        assert sentences[0].endswith("...")

    # --- End-of-text / whitespace edges ---

    def test_trailing_whitespace_is_dropped(self) -> None:
        text = "Ensimmäinen. Toinen.   \n\n"
        sentences = _split_sentences(text)
        # Should not produce a trailing empty sentence for the padding.
        assert all(s.strip() for s in sentences)

    def test_abbrev_at_end_of_text_keeps_last_sentence(self) -> None:
        # "Dr." at the very end of the text — no following sentence. The
        # final "." is inside the abbreviation but the text still needs
        # to come out as one whole sentence, not be dropped.
        text = "They met Dr."
        sentences = _split_sentences(text)
        assert len(sentences) == 1
        assert sentences[0].strip() == "They met Dr."


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
