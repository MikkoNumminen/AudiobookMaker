"""Finnish legal-citation normalization for TTS.

Finnish legal text is dense with tokens a generic TTS normalizer mishandles:
the section sign ``§``, law abbreviations (``PL``, ``LsL``, ``SHL`` …),
statute numbers (``417/2007``), article and moment references, and page
ranges. Read naively, ``PL 10 §`` becomes "pee-äl kymmenen <silence>" and
``586/1996`` becomes "586 slash 1996" (or is dropped). This module rewrites
those citations into plain spoken Finnish so the model reads them correctly.

It is deliberately CONTEXT-AWARE: a law abbreviation is only expanded when it
sits in a citation (directly before a number + ``§`` / ``art.`` / a statute
number). That keeps ``PL 15`` (a postilokero / P.O. box) from turning into
"perustuslain 15". Output is plain Finnish words plus bare digits, which the
main Finnish normalizer then reads with num2words — so this runs as an early
pass and composes with everything downstream.

Reading conventions chosen for clarity over formal register:
- ``10 §``        -> ``pykälä 10``    (read "pykälä kymmenen")
- ``4 §:n 2 mom`` -> ``pykälä 4 momentti 2``
- ``32.1 §``      -> ``pykälä 32 momentti 1``
- ``MK 2:1``      -> ``maakaaren luku 2 pykälä 1``
- ``8 art.``      -> ``artikla 8``
- ``417/2007``    -> ``417 kautta 2007``
- ``2024/552``    -> ``2024 kautta 552``
- ``KKO 2010:23`` -> ``KKO 2010 numero 23``
- ``s. 56-77``    -> ``sivut 56-77``

Every form puts the noun BEFORE the cardinal ("pykälä 10", "luku 2"). Two
reasons: Finnish ordinal + case agreement is not something num2words can
produce reliably ("2 luvun" would need "kahden luvun"), and the governor
table in ``data/fi_governors.yaml`` reads case off the word to the LEFT of a
number, so noun-first is the order that already has grammatical support.
That is why this module does not emit the more idiomatic written form
"maakaaren 2 luvun 1 pykälä".
"""

from __future__ import annotations

import re

# Law abbreviation -> (nominative, genitive) spoken forms. Genitive is used in
# the section/article context ("perustuslain pykälä 10"); nominative in a
# bare statute-number citation ("lastensuojelulaki 417 kautta 2007"). Extend
# this map as new abbreviations show up in source documents.
#
# Matching is CASE-SENSITIVE (no re.IGNORECASE below), which is what keeps the
# lowercase currency abbreviation `mk` (markka) clear of `MK` (maakaari).
#
# Laws whose real name is "laki <something>sta" get a genitive of the form
# "<something>sta annetun lain", the way a Finnish lawyer reads it aloud.
_LAW_ABBR: dict[str, tuple[str, str]] = {
    # Constitutional / administrative / social welfare
    "PL": ("perustuslaki", "perustuslain"),
    "EIS": ("Euroopan ihmisoikeussopimus", "Euroopan ihmisoikeussopimuksen"),
    "LsL": ("lastensuojelulaki", "lastensuojelulain"),
    "SHL": ("sosiaalihuoltolaki", "sosiaalihuoltolain"),
    "POL": ("perusopetuslaki", "perusopetuslain"),
    "OHL": ("oppilas- ja opiskelijahuoltolaki", "oppilas- ja opiskelijahuoltolain"),
    "HOL": (
        "laki oikeudenkäynnistä hallintoasioissa",
        "oikeudenkäynnistä hallintoasioissa annetun lain",
    ),
    "HL": ("hallintolaki", "hallintolain"),
    "HLL": ("hallintolainkäyttölaki", "hallintolainkäyttölain"),
    "PeVL": ("perustuslakivaliokunnan lausunto", "perustuslakivaliokunnan lausunnon"),
    "KHO": ("korkein hallinto-oikeus", "korkeimman hallinto-oikeuden"),
    # Contract / obligation law
    "OikTL": ("oikeustoimilaki", "oikeustoimilain"),
    "LahjaL": ("lahjanlupauslaki", "lahjanlupauslain"),
    "KL": ("kauppalaki", "kauppalain"),
    "KSL": ("kuluttajansuojalaki", "kuluttajansuojalain"),
    "KorkoL": ("korkolaki", "korkolain"),
    "VKL": ("velkakirjalaki", "velkakirjalain"),
    "VahKorvL": ("vahingonkorvauslaki", "vahingonkorvauslain"),
    "VahL": ("vahingonkorvauslaki", "vahingonkorvauslain"),
    # Property / real estate / tenancy
    "MK": ("maakaari", "maakaaren"),
    "MVL": ("maanvuokralaki", "maanvuokralain"),
    "AsKL": ("asuntokauppalaki", "asuntokauppalain"),
    "AsHVL": (
        "laki asuinhuoneiston vuokrauksesta",
        "asuinhuoneiston vuokrauksesta annetun lain",
    ),
    "LiikHVL": (
        "laki liikehuoneiston vuokrauksesta",
        "liikehuoneiston vuokrauksesta annetun lain",
    ),
    "YhtOmL": ("yhteisomistuslaki", "yhteisomistuslain"),
    "VarSiirtoVL": ("varainsiirtoverolaki", "varainsiirtoverolain"),
    "YrKiinL": ("yrityskiinnityslaki", "yrityskiinnityslain"),
    # Companies
    "OYL": ("osakeyhtiölaki", "osakeyhtiölain"),
    "AsOYL": ("asunto-osakeyhtiölaki", "asunto-osakeyhtiölain"),
    # Insolvency / enforcement
    "KonkL": ("konkurssilaki", "konkurssilain"),
    "UK": ("ulosottokaari", "ulosottokaaren"),
    "TakSL": (
        "laki takaisinsaannista konkurssipesään",
        "takaisinsaannista konkurssipesään annetun lain",
    ),
    # Family / inheritance / guardianship
    "AL": ("avioliittolaki", "avioliittolain"),
    "PK": ("perintökaari", "perintökaaren"),
    "HolTL": ("laki holhoustoimesta", "holhoustoimesta annetun lain"),
    # Criminal / employment
    "RL": ("rikoslaki", "rikoslain"),
    "TSL": ("työsopimuslaki", "työsopimuslain"),
}

_ABBR_ALT = "|".join(sorted(_LAW_ABBR, key=len, reverse=True))

# Abbreviations kept OUT of the chapter-shorthand pass (the "MK 2:1" form).
# That pass claims any `<abbr> N:M`, which is a wide net, so a token that is
# far more often something else in ordinary prose must not opt in:
#   KHO / KKO — courts, whose own citations are exactly `<court> YYYY:NN`
#   UK        — "United Kingdom" vastly outnumbers "ulosottokaari" before a
#               colon pair, and `UK 4 §` still works through the section pass
# They stay in _LAW_ABBR: only the chapter pass excludes them.
_NON_CHAPTER_ABBR = frozenset({"KHO", "KKO", "UK"})
_CHAPTER_ABBR_ALT = "|".join(
    sorted(
        (a for a in _LAW_ABBR if a not in _NON_CHAPTER_ABBR),
        key=len,
        reverse=True,
    )
)

# A "moment" token: "momentti", "momentin", "momenttina", "mom", "mom.".
# Full-word forms MUST precede the "mom" abbreviation, otherwise the regex
# alternation matches "mom" as a prefix of "momentti" and leaves "entti".
_MOM = r"(?:moment\w*|mom\.?)"
# An "article" token, same longest-first rule for "artikla" vs "art".
_ART = r"(?:artikl\w*|art\.?)"
# A "chapter" token. Finnish has two stems for this word: `luku` in the
# nominative and `luvu-` in the inflected forms (luvun, luvussa, luvusta).
_CHAPTER = r"(?:luvu\w*|luku\w*)"

# An optional Finnish case ending glued on with a colon: `§:n`, `§:ssä`,
# `2:1:ssä`. Whatever case the writer put on the citation, it belongs to the
# citation as a whole, and there is nowhere to put it once the reference has
# been rewritten noun-first. Consuming it is what keeps it off the NUMBER:
# left in place, `LsL 4 §:ssä` reached the numeral pass and came out
# "pykälä neljässä" (inessive on the digit), and `MK 2:1:ssä` came out
# "pykälä yhdessä", which a listener hears as "together".
_CLITIC = r"(?::[a-zäöåA-ZÄÖÅ]{1,4})?"

# A number that does not continue a decimal or a longer figure. Without this
# guard the chapter passes matched the tail of "1.5 luvun 3 §" and emitted
# "1.luku 5 pykälä 3", gluing a period between two spoken words.
_NUM_START = r"(?<![.,\d])"

# Section written together with its moment as "32.1 §" (section 32, moment 1)
# — the compact form of "32 §:n 1 momentti". Shared by the with-abbreviation
# and standalone passes; both must run BEFORE the plain-section pass, which
# would otherwise claim only the "1 §" tail and strand the "32." in front of
# it, gluing a literal period into a spoken word.
_SEC_DOT_MOM = rf"(?P<sec>\d+)\.(?P<mom>\d+)\s*§{_CLITIC}"

# --- compiled patterns ------------------------------------------------------
#
# Pass Z runs once per synthesis chunk, so a book-length conversion enters this
# module thousands of times. Six of these interpolate the ~130-character
# abbreviation alternation, which is constant after import; building them at
# module level keeps that cost off the per-chunk path.

_STATUTE_NUM_YEAR_RE = re.compile(r"\b(\d{1,4})/((?:19|20)\d{2})\b")
_STATUTE_YEAR_NUM_RE = re.compile(r"\b((?:19|20)\d{2})/(\d{1,4})\b")

# Court decision citations. Finlex prints these with a colon after the court
# ("KKO:2010:23"); textbooks tend to use a space ("KKO 2010:23"). Both forms
# occur, so the separator is either.
#
# The court abbreviation is deliberately LEFT AS WRITTEN — a Finnish reader
# says "koo-koo-oo", which is exactly what the acronym pass produces
# downstream, so expanding it to "korkeimman oikeuden ratkaisu" would be a
# rewrite rather than a reading. Only the colon needs a word: left alone it
# reached Pass Y, which turns a colon between two letters into a hyphen, and
# the citation came out as "kaksituhatta kymmenen-kaksikymmentä kolme".
#
# The year is any four digits rather than 19xx/20xx so that an older or
# mistyped citation still lands here rather than falling through to a pass
# that would read it as a chapter.
_COURT_CITATION_RE = re.compile(r"\b(KKO|KHO)[:\s]\s*(\d{4}):(\d{1,4})\b")

# A series designator after a year: "KM 1965:A 3" is committee report series A.
# The letter is uppercase and the number of the report follows it. Finnish case
# endings are lowercase, so this shape cannot be a case ending — but the
# numeral pass downstream lowercases before looking up, which turned `:A` into
# the partitive and deleted the letter.
_SERIES_LETTER_RE = re.compile(r"\b((?:19|20)\d{2}):([A-ZÄÖÅ])(?=\s+\d)")

_ABBR_CHAPTER_COLON_RE = re.compile(
    rf"\b(?P<abbr>{_CHAPTER_ABBR_ALT})\s+{_NUM_START}(?P<ch>\d+):(?P<sec>\d+)"
    rf"(?:\.(?P<mom>\d+))?(?:\s*§)?{_CLITIC}"
)
_ABBR_CHAPTER_WORD_RE = re.compile(
    rf"\b(?P<abbr>{_ABBR_ALT})\s+{_NUM_START}(?P<ch>\d+)\s+{_CHAPTER}\s+"
    rf"(?P<sec>\d+)\s*§{_CLITIC}(?:\s+(?P<mom>\d+)\s*{_MOM})?"
)
_CHAPTER_WORD_RE = re.compile(
    rf"\b{_NUM_START}(?P<ch>\d+)\s+{_CHAPTER}\s+(?P<sec>\d+)\s*§{_CLITIC}"
    rf"(?:\s+(?P<mom>\d+)\s*{_MOM})?"
)
_ABBR_SEC_DOT_MOM_RE = re.compile(rf"\b(?P<abbr>{_ABBR_ALT})\s+{_SEC_DOT_MOM}")
_ABBR_TWO_SECTIONS_RE = re.compile(
    rf"\b(?P<abbr>{_ABBR_ALT})\s+(?P<first>\d+)\s+ja\s+(?P<second>\d+)"
    rf"\s*§{_CLITIC}"
)
_ABBR_SECTION_RE = re.compile(
    rf"\b(?P<abbr>{_ABBR_ALT})\s+(?P<sec>\d+)\s*§{_CLITIC}"
    rf"(?:\s+(?P<mom>\d+)\s*{_MOM})?"
)
_ABBR_ARTICLE_RE = re.compile(
    rf"\b(?P<abbr>{_ABBR_ALT})\s+(?P<art>\d+)\s*{_ART}"
)
_ABBR_STATUTE_RE = re.compile(
    rf"\b(?P<abbr>{_ABBR_ALT})\s+(?=\d+\s+kautta\s+\d{{4}})"
)
_SEC_DOT_MOM_RE = re.compile(_SEC_DOT_MOM)
_SECTION_RE = re.compile(
    rf"\b(?P<sec>\d+)\s*§{_CLITIC}(?:\s+(?P<mom>\d+)\s*{_MOM})?"
)
_ARTICLE_RE = re.compile(rf"\b(?P<art>\d+)\s*{_ART}")
_PAGE_RANGE_RE = re.compile(r"\bs\.\s*(\d+)\s*[-–]\s*(\d+)")
_DEDUP_RE = re.compile(
    r"(\w+)\s+\((\w+)\s+((?:pykälä|artikla|luku|\d)[^)]*)\)"
)
_DEDUP_MULTIWORD_RE = re.compile(
    r"(\w[\w-]+(?:\s+\w[\w-]+){1,3})\s+\(\1\s+(?=pykälä|artikla|luku|\d)"
)


def _genitive(abbr: str) -> str:
    return _LAW_ABBR[abbr][1]


def _nominative(abbr: str) -> str:
    return _LAW_ABBR[abbr][0]


def _abbr_chapter(m: re.Match[str]) -> str:
    """`MK 2:1` / `MK 2 luvun 1 §` -> `maakaaren luku 2 pykälä 1`."""
    out = (
        f"{_genitive(m.group('abbr'))} luku {m.group('ch')} "
        f"pykälä {m.group('sec')}"
    )
    if m.group("mom"):
        out += f" momentti {m.group('mom')}"
    return out


def _chapter_only(m: re.Match[str]) -> str:
    """`2 luvun 1 §` -> `luku 2 pykälä 1` (law already named in prose)."""
    out = f"luku {m.group('ch')} pykälä {m.group('sec')}"
    if m.group("mom"):
        out += f" momentti {m.group('mom')}"
    return out


def _abbr_section(m: re.Match[str]) -> str:
    """`LsL 4 §:n 2 momentti` -> `lastensuojelulain pykälä 4 momentti 2`."""
    out = f"{_genitive(m.group('abbr'))} pykälä {m.group('sec')}"
    if m.group("mom"):
        out += f" momentti {m.group('mom')}"
    return out


def expand_legal_citations(text: str) -> str:
    """Rewrite Finnish legal citations into plain spoken Finnish + digits.

    Idempotent on its own output (the rewritten forms contain no ``§`` /
    ``art.`` / statute-slash patterns that would re-trigger a pass). Safe on
    non-legal text: every pass requires a citation-shaped context.
    """
    if not text:
        return text

    # 1. Statute numbers NNN/YYYY -> "NNN kautta YYYY" (years 1900-2099). Done
    #    first so the slash is gone before any number pass sees it, and before
    #    the abbreviation passes (which may sit right in front: "LsL 417/2007").
    text = _STATUTE_NUM_YEAR_RE.sub(r"\1 kautta \2", text)

    # 2. The amendment form puts the date FIRST: "muutettu lailla
    #    18.10.2024/552". Pass 1 only matches number/year, so this slash
    #    survived all the way to the synth. Runs after pass 1 so it can never
    #    steal a NNN/YYYY citation.
    #
    #    Only ONE slash per run is claimed: a bare "2024/12/25" keeps its
    #    second slash. Sequences of slashed numbers are dates rather than
    #    citations, and a general digit/digit rule would swallow fractions
    #    ("1/2 annoksesta"), which the tests pin as untouchable.
    text = _STATUTE_YEAR_NUM_RE.sub(r"\1 kautta \2", text)

    # 3. Court decision citations. The court abbreviations are excluded from
    #    the chapter pass below (_NON_CHAPTER_ABBR), so this does not depend on
    #    running first — but it is cheap and belongs with the other
    #    "a separator needs a word" rewrites.
    text = _COURT_CITATION_RE.sub(r"\1 \2 numero \3", text)

    # 4. A series designator after a year: "KM 1965:A 3" is committee report
    #    series A. Handled here, before any numeral pass sees the colon.
    text = _SERIES_LETTER_RE.sub(r"\1 \2", text)

    # 5. Chapter:section shorthand — "MK 2:1" is chapter 2, section 1 of the
    #    maakaari, and "MK 13:4.1" adds moment 1. Requires a known law
    #    abbreviation in front on purpose: a bare "2:1" is far more likely a
    #    ratio or a score, and Pass X (colon ratios) is welcome to keep those.
    #    Before this pass "MK 2:1" reached Pass X and was read "kaksi yhteen".
    #    A trailing "§" and any case clitic are consumed so neither the plain-
    #    section pass nor the numeral pass can claim what is left over.
    text = _ABBR_CHAPTER_COLON_RE.sub(_abbr_chapter, text)

    # 6. The same reference with the chapter word SPELLED OUT:
    #    "MK 2 luvun 1 §". The plain-section pass below only converts the
    #    "1 §" tail, which left the abbreviation unexpanded ("äm koo kaksi
    #    luvun pykälä yksi") and the chapter number stranded in front of a
    #    genitive it does not agree with ("kaksi luvun" should be "kahden
    #    luvun"). Reordering to noun-first fixes the agreement, because the
    #    governor table reads case off the word to the number's left.
    #
    #    The alternation covers luku / lukuun / luvun / luvussa / luvusta.
    #    It is written as two stems rather than `lu[kv]\w*` so that unrelated
    #    `luva-` words ("luvalla") cannot be mistaken for a chapter.
    text = _ABBR_CHAPTER_WORD_RE.sub(_abbr_chapter, text)

    # 7. Same shape, law already named in prose ("Maakaaren 2 luvun 1 §").
    #    Requires the chapter word BETWEEN two numbers and a section sign
    #    after, which no ordinary sentence produces by accident.
    text = _CHAPTER_WORD_RE.sub(_chapter_only, text)

    # 8. Law abbreviation + the compact section.moment form: "OikTL 32.1 §"
    #    -> "oikeustoimilain pykälä 32 momentti 1".
    text = _ABBR_SEC_DOT_MOM_RE.sub(
        lambda m: (
            f"{_genitive(m.group('abbr'))} pykälä {m.group('sec')} "
            f"momentti {m.group('mom')}"
        ),
        text,
    )

    # 9. Two sections of one law under a single abbreviation: "PL 10 ja 22 §".
    #    The section pass below needs the "§" directly after the number, so
    #    without this the abbreviation went unexpanded and only the second
    #    number became a "pykälä".
    text = _ABBR_TWO_SECTIONS_RE.sub(
        lambda m: (
            f"{_genitive(m.group('abbr'))} pykälä {m.group('first')} "
            f"ja pykälä {m.group('second')}"
        ),
        text,
    )

    # 10. Law abbreviation + section, possibly with a moment:
    #    "LsL 4 §:n 2 momentti" -> "lastensuojelulain pykälä 4 momentti 2"
    #    "PL 10 §"              -> "perustuslain pykälä 10"
    text = _ABBR_SECTION_RE.sub(_abbr_section, text)

    # 11. Law abbreviation + article: "EIS 8 art." -> "... artikla 8".
    text = _ABBR_ARTICLE_RE.sub(
        lambda m: f"{_genitive(m.group('abbr'))} artikla {m.group('art')}",
        text,
    )

    # 12. Law abbreviation directly before a statute number (already "kautta"-ed
    #    in pass 1), typically the parenthetical that defines the abbreviation:
    #    "(LsL 417 kautta 2007)" -> "(lastensuojelulaki 417 kautta 2007)".
    text = _ABBR_STATUTE_RE.sub(
        lambda m: f"{_nominative(m.group('abbr'))} ",
        text,
    )

    # 13. Standalone section.moment, law name already spelled out in prose
    #    ("sovitellaan 36.2 §:n nojalla"). MUST precede the plain-section pass
    #    (14), which would otherwise match only the "2 §" tail.
    text = _SEC_DOT_MOM_RE.sub(
        lambda m: f"pykälä {m.group('sec')} momentti {m.group('mom')}",
        text,
    )

    # 14. Standalone section/article (law name already spelled out, e.g.
    #    "Lastensuojelulain 31 §"): convert the symbol after the number.
    text = _SECTION_RE.sub(
        lambda m: (
            f"pykälä {m.group('sec')}"
            + (f" momentti {m.group('mom')}" if m.group("mom") else "")
        ),
        text,
    )
    text = _ARTICLE_RE.sub(
        lambda m: f"artikla {m.group('art')}",
        text,
    )

    # 15. Page ranges: "s. 56-77" / "s. 56–77" -> "sivut 56-77" (plural form;
    #    the existing normalizer otherwise emits singular "sivu" and the range
    #    reads as two stray numbers). A lone "s. 42" stays for Pass E.
    text = _PAGE_RANGE_RE.sub(r"sivut \1-\2", text)

    # 16. Drop a redundant law name inside a parenthetical citation when the same
    #    (single-word) law was just named before the paren, e.g.
    #    "perustuslain (perustuslain pykälä 10)" -> "perustuslain (pykälä 10)"
    #    and "lastensuojelulaki (lastensuojelulaki 417 kautta 2007)" ->
    #    "lastensuojelulaki (417 kautta 2007)". Multi-word law names are left
    #    intact (they rarely repeat the immediately-preceding word).
    def _dedup(m: re.Match[str]) -> str:
        before, lawin, rest = m.group(1), m.group(2), m.group(3)
        a, b = before.lower(), lawin.lower()
        common = 0
        for ca, cb in zip(a, b):
            if ca != cb:
                break
            common += 1
        if common >= 6:  # same law in a different case form
            return f"{before} ({rest})"
        return m.group(0)

    text = _DEDUP_RE.sub(_dedup, text)

    # 17. Same, for an exactly-repeated MULTI-word law name (e.g. "Euroopan
    #    ihmisoikeussopimuksen (Euroopan ihmisoikeussopimuksen artikla 8)").
    text = _DEDUP_MULTIWORD_RE.sub(r"\1 (", text)

    return text
