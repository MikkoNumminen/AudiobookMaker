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
