"""Tests for Finnish legal-citation normalization (src.tts_normalizer_fi_legal).

Uses public statutory references (section numbers, statute numbers, law
abbreviations) — not copyrighted text. Asserts the legal-pass output directly
so the tests don't depend on num2words digit→word conversion.
"""

from __future__ import annotations

import pytest

from src.tts_normalizer_fi_legal import expand_legal_citations


# --- the section sign and law abbreviations --------------------------------


def test_abbrev_plus_section() -> None:
    assert expand_legal_citations("PL 10 §") == "perustuslain pykälä 10"


def test_abbrev_plus_section_genitive_with_moment() -> None:
    out = expand_legal_citations("LsL 4 §:n 2 momentti")
    assert out == "lastensuojelulain pykälä 4 momentti 2"


def test_spelled_out_law_plus_section() -> None:
    # Law already named in prose; only the symbol needs expanding.
    out = expand_legal_citations("Lastensuojelulain 4 §:n 2 momentti")
    assert out == "Lastensuojelulain pykälä 4 momentti 2"


def test_article_expands() -> None:
    assert expand_legal_citations("EIS 8 art.") == (
        "Euroopan ihmisoikeussopimuksen artikla 8"
    )


@pytest.mark.parametrize(
    "abbr,expected",
    [
        ("SHL 26 §", "sosiaalihuoltolain pykälä 26"),
        ("POL 41 §", "perusopetuslain pykälä 41"),
        ("HOL 37 §", "oikeudenkäynnistä hallintoasioissa annetun lain pykälä 37"),
    ],
)
def test_various_law_abbreviations(abbr: str, expected: str) -> None:
    assert expand_legal_citations(abbr) == expected


# --- civil-law abbreviations ------------------------------------------------
#
# The original table covered constitutional / administrative / social welfare
# law only. A civil-law source document reached the synth with every one of
# these still spelled as a mixed-case token ("OikTL"), which no acronym pass
# claims and no engine reads.


@pytest.mark.parametrize(
    "src,expected",
    [
        ("OikTL 36 §", "oikeustoimilain pykälä 36"),
        ("LahjaL 1 §", "lahjanlupauslain pykälä 1"),
        ("KSL 2 §", "kuluttajansuojalain pykälä 2"),
        ("KL 17 §", "kauppalain pykälä 17"),
        ("KorkoL 4 §", "korkolain pykälä 4"),
        ("VKL 13 §", "velkakirjalain pykälä 13"),
        ("OYL 5 §", "osakeyhtiölain pykälä 5"),
        ("AsOYL 6 §", "asunto-osakeyhtiölain pykälä 6"),
        ("RL 28 §", "rikoslain pykälä 28"),
        ("AL 34 §", "avioliittolain pykälä 34"),
        ("TSL 7 §", "työsopimuslain pykälä 7"),
        ("MVL 19 §", "maanvuokralain pykälä 19"),
        ("AsKL 3 §", "asuntokauppalain pykälä 3"),
        ("KonkL 2 §", "konkurssilain pykälä 2"),
        ("UK 4 §", "ulosottokaaren pykälä 4"),
        ("PK 12 §", "perintökaaren pykälä 12"),
        ("MK 11 §", "maakaaren pykälä 11"),
        ("YhtOmL 9 §", "yhteisomistuslain pykälä 9"),
        ("VarSiirtoVL 1 §", "varainsiirtoverolain pykälä 1"),
        ("YrKiinL 2 §", "yrityskiinnityslain pykälä 2"),
        ("VahL 5 §", "vahingonkorvauslain pykälä 5"),
        ("VahKorvL 5 §", "vahingonkorvauslain pykälä 5"),
        (
            "AsHVL 8 §",
            "asuinhuoneiston vuokrauksesta annetun lain pykälä 8",
        ),
        (
            "LiikHVL 8 §",
            "liikehuoneiston vuokrauksesta annetun lain pykälä 8",
        ),
        (
            "TakSL 10 §",
            "takaisinsaannista konkurssipesään annetun lain pykälä 10",
        ),
        ("HolTL 29 §", "holhoustoimesta annetun lain pykälä 29"),
    ],
)
def test_civil_law_abbreviations(src: str, expected: str) -> None:
    assert expand_legal_citations(src) == expected


def test_longer_abbreviation_wins_over_its_suffix() -> None:
    """`AsOYL` must not be read as `OYL`, nor `VahKorvL` as `VahL`."""
    assert expand_legal_citations("AsOYL 1 §") == "asunto-osakeyhtiölain pykälä 1"
    assert expand_legal_citations("VahKorvL 1 §") == "vahingonkorvauslain pykälä 1"


def test_lowercase_markka_is_not_the_maakaari() -> None:
    """`mk` is the old currency; only uppercase `MK` is the maakaari.

    Matching is case-sensitive precisely so this stays true.
    """
    assert expand_legal_citations("hinta 50 mk") == "hinta 50 mk"


# --- chapter:section shorthand (rule 6) -------------------------------------
#
# Finnish legal writing compresses "maakaaren 2 luvun 1 §" to "MK 2:1". The
# colon used to reach Pass X, the colon-ratio pass, and the citation was read
# as the ratio "kaksi yhteen".


@pytest.mark.parametrize(
    "src,expected",
    [
        ("MK 2:1", "maakaaren luku 2 pykälä 1"),
        ("MK 13:4.1", "maakaaren luku 13 pykälä 4 momentti 1"),
        ("KSL 5:10.1", "kuluttajansuojalain luku 5 pykälä 10 momentti 1"),
        ("PK 3:1", "perintökaaren luku 3 pykälä 1"),
        # A trailing section sign is consumed rather than left to become a
        # second, stray "pykälä".
        ("MK 2:1 §", "maakaaren luku 2 pykälä 1"),
    ],
)
def test_chapter_section_shorthand(src: str, expected: str) -> None:
    assert expand_legal_citations(src) == expected


# --- the same reference with the chapter word spelled out -------------------


@pytest.mark.parametrize(
    "src,expected",
    [
        ("MK 2 luvun 1 §", "maakaaren luku 2 pykälä 1"),
        ("MK 13 luvun 4 §:n 1 momentti", "maakaaren luku 13 pykälä 4 momentti 1"),
        ("KSL 5 luvussa 10 §", "kuluttajansuojalain luku 5 pykälä 10"),
        # Law spelled out in prose rather than abbreviated.
        ("Maakaaren 2 luvun 1 §", "Maakaaren luku 2 pykälä 1"),
        ("2 luvussa 3 §", "luku 2 pykälä 3"),
    ],
)
def test_spelled_out_chapter_word(src: str, expected: str) -> None:
    assert expand_legal_citations(src) == expected


def test_spelled_out_chapter_expands_the_abbreviation() -> None:
    """Regression: this form used to skip the abbreviation table entirely.

    The plain-section pass claimed only the "1 §" tail, so "MK" was never
    recognised as a law and reached the synth to be spelled "äm koo".
    """
    out = expand_legal_citations("MK 2 luvun 1 §")
    assert "MK" not in out
    assert out.startswith("maakaaren ")


def test_chapter_number_precedes_its_noun_after_rewriting() -> None:
    """`2 luvun` needs the genitive `kahden luvun`, which num2words will not
    produce. Reordering to `luku 2` lets the governor table read nominative
    off the word to the number's left instead.
    """
    out = expand_legal_citations("Maakaaren 2 luvun 1 §")
    assert "luku 2" in out
    assert "2 luvun" not in out


def test_unrelated_luva_word_is_not_a_chapter() -> None:
    """`luvalla` ("with permission") shares no stem with `luku`/`luvu-`."""
    out = expand_legal_citations("myyty 3 luvalla 5 §")
    assert "luvalla" in out
    assert "luku" not in out


def test_bare_ratio_is_left_for_the_ratio_pass() -> None:
    """Without a law abbreviation in front, `2:1` is a ratio or a score."""
    assert expand_legal_citations("suhde 2:1") == "suhde 2:1"


def test_chapter_shorthand_in_a_sentence() -> None:
    out = expand_legal_citations("Kauppakirjasta säädetään MK 2:1:ssä.")
    assert "maakaaren luku 2 pykälä 1" in out


# --- the compact section.moment form (rule 4) -------------------------------


@pytest.mark.parametrize(
    "src,expected",
    [
        ("OikTL 32.1 §", "oikeustoimilain pykälä 32 momentti 1"),
        ("KSL 5.2 §", "kuluttajansuojalain pykälä 5 momentti 2"),
        # Law already named in prose: only the number form needs converting.
        ("36.2 §", "pykälä 36 momentti 2"),
        ("36.2 §:n", "pykälä 36 momentti 2"),
    ],
)
def test_section_dot_moment(src: str, expected: str) -> None:
    assert expand_legal_citations(src) == expected


def test_section_dot_moment_leaves_no_glued_period() -> None:
    """The defect: `32.1 §` came out as `32.pykälä 1`.

    The plain-section pass claimed only the `1 §` tail, stranding `32.` in
    front of a word and gluing a literal period into the middle of it.
    """
    out = expand_legal_citations("OikTL 32.1 §")
    assert ".pykälä" not in out
    assert "momentti 1" in out


# --- court decision citations (rule 7) --------------------------------------


@pytest.mark.parametrize(
    "src,expected",
    [
        ("KKO 2010:23", "KKO 2010 numero 23"),
        ("KHO 1985:12", "KHO 1985 numero 12"),
    ],
)
def test_court_citation_colon_becomes_a_word(src: str, expected: str) -> None:
    assert expand_legal_citations(src) == expected


def test_court_abbreviation_is_kept_as_written() -> None:
    """Rule 7 keeps case citations in their original form.

    A Finnish reader says "koo-koo-oo", which the downstream acronym pass
    produces from the letters. Expanding to "korkeimman oikeuden ratkaisu"
    would be a rewrite, not a reading.
    """
    assert expand_legal_citations("KKO 2010:23").startswith("KKO ")


@pytest.mark.parametrize(
    "src",
    [
        "KKO 1984 II 125",   # older citation style, no colon
        "KHO 1985 A II 75",
    ],
)
def test_older_citation_styles_are_left_alone(src: str) -> None:
    assert expand_legal_citations(src) == src


# --- amendment statute numbers (the date-first slash) -----------------------


def test_amendment_slash_year_first() -> None:
    """`18.10.2024/552` — the date comes first, so the year is on the LEFT."""
    out = expand_legal_citations("muutettu lailla 18.10.2024/552")
    assert out == "muutettu lailla 18.10.2024 kautta 552"


def test_number_first_statute_still_wins() -> None:
    """The original NNN/YYYY form must not be re-read by the new pass."""
    assert expand_legal_citations("417/2007") == "417 kautta 2007"


def test_no_slash_survives_an_amendment_reference() -> None:
    src = "MK 13:3.1 muutettu lailla 18.10.2024/552, voimaan 1.11.2024."
    assert "/" not in expand_legal_citations(src)


# --- statute numbers (the slash) -------------------------------------------


def test_statute_number_slash_becomes_kautta() -> None:
    assert expand_legal_citations("(586/1996)") == "(586 kautta 1996)"
    assert expand_legal_citations("417/2007") == "417 kautta 2007"


def test_journal_issue_slash() -> None:
    assert "1 kautta 2017" in expand_legal_citations("Oikeus 1/2017")


def test_non_year_slash_is_left_alone() -> None:
    # A fraction-like "1/2" must not become "1 kautta 2" (only NNN/19xx|20xx).
    assert expand_legal_citations("1/2 annoksesta") == "1/2 annoksesta"


# --- page ranges ------------------------------------------------------------


def test_page_range() -> None:
    assert expand_legal_citations("s. 56-77") == "sivut 56-77"
    assert expand_legal_citations("s. 226–243") == "sivut 226-243"


# --- redundant law-name dedup ----------------------------------------------


def test_dedup_single_word_law() -> None:
    out = expand_legal_citations("perustuslain (PL 10 §)")
    assert out == "perustuslain (pykälä 10)"


def test_dedup_statute_number_in_parens() -> None:
    out = expand_legal_citations("lastensuojelulaki (LsL 417/2007)")
    assert out == "lastensuojelulaki (417 kautta 2007)"


def test_dedup_multiword_law() -> None:
    out = expand_legal_citations(
        "Euroopan ihmisoikeussopimuksen (EIS 8 art.)"
    )
    assert out == "Euroopan ihmisoikeussopimuksen (artikla 8)"


def test_no_dedup_when_different_law() -> None:
    # Preceding word is not the law name -> keep the full expansion.
    out = expand_legal_citations("edellytykset (LsL 40 §)")
    assert out == "edellytykset (lastensuojelulain pykälä 40)"


# --- context-awareness / safety --------------------------------------------


def test_postal_box_not_expanded() -> None:
    # "PL 15" with no § / art. / statute number is a postilokero, not a law.
    assert expand_legal_citations("PL 15, 00100 Helsinki") == (
        "PL 15, 00100 Helsinki"
    )


def test_empty_and_plain_text_unchanged() -> None:
    assert expand_legal_citations("") == ""
    assert expand_legal_citations("Tavallista tekstiä ilman lakeja.") == (
        "Tavallista tekstiä ilman lakeja."
    )


def test_idempotent() -> None:
    s = (
        "perustuslain (PL 10 §), EIS 8 art., LsL 4 §:n 2 momentti, "
        "(586/1996), s. 56-77"
    )
    once = expand_legal_citations(s)
    assert expand_legal_citations(once) == once


@pytest.mark.parametrize(
    "src",
    [
        "MK 2:1",
        "MK 13:4.1",
        "MK 2 luvun 1 §",
        "Maakaaren 2 luvun 1 §",
        "OikTL 32.1 §",
        "KKO 2010:23",
        "muutettu lailla 18.10.2024/552",
        "MK 13:3.1 muutettu lailla 18.10.2024/552, voimaan 1.11.2024.",
    ],
)
def test_new_forms_are_idempotent(src: str) -> None:
    """A second pass must be a no-op.

    The pass runs once today, but an idempotence break is how a future
    reordering would silently corrupt output ("luku 2" re-read as a chapter).
    """
    once = expand_legal_citations(src)
    assert expand_legal_citations(once) == once


def test_moment_word_not_truncated_to_mom() -> None:
    # Regression: the _MOM alternation must not match "mom" inside "momentti"
    # and leave a dangling "entti" fragment ("... 2entti").
    out = expand_legal_citations("LsL 4 §:n 2 momentti")
    assert "entti" not in out.replace("momentti", "")
    assert "momentti 2" in out


# --- integration: the pass is wired into the Finnish pipeline ---------------


def test_wired_into_pipeline() -> None:
    from src.tts_normalizer import normalize_text
    out = normalize_text("perustuslain (PL 10 §) turvaa", "fi")
    assert "§" not in out
    assert "pykälä" in out
    assert "P L" not in out  # abbreviation not spelled letter-by-letter


# Synthetic sentences in the shape of a civil-law textbook. No third-party
# text: every citation here is a bare statutory reference.
_END_TO_END = [
    "Sopimusta voidaan sovitella OikTL 36 §:n nojalla.",
    "Kaupasta säädetään MK 2:1:ssä ja MK 13:4.1:ssä.",
    "Virheestä säädetään KSL 5:10.1 kohdassa.",
    "Ratkaisussa KKO 2010:23 arvioitiin kysymystä.",
    "OikTL 32.1 § koskee tahdonilmaisua.",
    "MK 13:3.1 muutettu lailla 18.10.2024/552.",
]


@pytest.mark.parametrize("src", _END_TO_END)
def test_no_raw_symbol_reaches_the_engine(src: str) -> None:
    """None of §, a stray colon, or a slash may survive to the synth.

    Each of these is silently unreadable: the engine either drops the glyph
    or reads it as a pause, so the citation loses its meaning without any
    warning in the log.
    """
    from src.tts_normalizer import normalize_text
    out = normalize_text(src, "fi")
    assert "§" not in out, out
    assert ":" not in out, out
    assert "/" not in out, out


@pytest.mark.parametrize("src", _END_TO_END)
def test_no_period_glued_inside_a_word(src: str) -> None:
    """`32.pykälä` — a period with letters on both sides is never speakable."""
    import re as _re
    from src.tts_normalizer import normalize_text
    out = normalize_text(src, "fi")
    assert not _re.search(r"[a-zäöå]\.[a-zäöå]", out), out
