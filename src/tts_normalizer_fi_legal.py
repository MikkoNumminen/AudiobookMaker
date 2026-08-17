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

# A "moment" token: "momentti", "momentin", "momenttina", "mom", "mom.".
# Full-word forms MUST precede the "mom" abbreviation, otherwise the regex
# alternation matches "mom" as a prefix of "momentti" and leaves "entti".
_MOM = r"(?:moment\w*|mom\.?)"
# An "article" token, same longest-first rule for "artikla" vs "art".
_ART = r"(?:artikl\w*|art\.?)"
# A "chapter" token. Finnish has two stems for this word: `luku` in the
# nominative and `luvu-` in the inflected forms (luvun, luvussa, luvusta).
_CHAPTER = r"(?:luvu\w*|luku\w*)"

# Section written together with its moment as "32.1 §" (section 32, moment 1)
# — the compact form of "32 §:n 1 momentti". Shared by the with-abbreviation
# and standalone passes; both must run BEFORE the plain-section pass, which
# would otherwise claim only the "1 §" tail and strand the "32." in front of
# it, gluing a literal period into a spoken word.
_SEC_DOT_MOM = r"(?P<sec>\d+)\.(?P<mom>\d+)\s*§(?::n)?"

# Court decision citations: "KKO 2010:23", "KHO 1985:12". The court
# abbreviation is deliberately LEFT AS WRITTEN — a Finnish reader says
# "koo-koo-oo", which is exactly what the acronym pass produces downstream,
# so expanding it to "korkeimman oikeuden ratkaisu" would be a rewrite rather
# than a reading. Only the colon needs a word: left alone it reached Pass Y,
# which turns a colon between two letters into a hyphen, and the citation came
# out as "kaksituhatta kymmenen-kaksikymmentä kolme".
_COURT_CITATION_RE = re.compile(r"\b(KKO|KHO)\s+((?:19|20)\d{2}):(\d{1,4})\b")


def _genitive(abbr: str) -> str:
    return _LAW_ABBR[abbr][1]


def _nominative(abbr: str) -> str:
    return _LAW_ABBR[abbr][0]


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
    text = re.sub(r"\b(\d{1,4})/((?:19|20)\d{2})\b", r"\1 kautta \2", text)

    # 2. The amendment form puts the date FIRST: "muutettu lailla
    #    18.10.2024/552". Pass 1 only matches number/year, so this slash
    #    survived all the way to the synth. Runs after pass 1 so it can never
    #    steal a NNN/YYYY citation.
    text = re.sub(r"\b((?:19|20)\d{2})/(\d{1,4})\b", r"\1 kautta \2", text)

    # 3. Court decision citations. No slash involved, so order relative to the
    #    statute passes does not matter; kept with them because it is the same
    #    "a separator needs a word" problem.
    text = _COURT_CITATION_RE.sub(r"\1 \2 numero \3", text)

    # 4. Chapter:section shorthand — "MK 2:1" is chapter 2, section 1 of the
    #    maakaari, and "MK 13:4.1" adds moment 1. Requires a known law
    #    abbreviation in front on purpose: a bare "2:1" is far more likely a
    #    ratio or a score, and Pass X (colon ratios) is welcome to keep those.
    #    Before this pass "MK 2:1" reached Pass X and was read "kaksi yhteen".
    #    A trailing "§" is consumed if present ("MK 2:1 §") so the plain-
    #    section pass below cannot turn the leftover symbol into a stray
    #    "pykälä".
    def _abbr_chapter(m: re.Match[str]) -> str:
        out = (
            f"{_genitive(m.group('abbr'))} luku {m.group('ch')} "
            f"pykälä {m.group('sec')}"
        )
        if m.group("mom"):
            out += f" momentti {m.group('mom')}"
        return out

    text = re.sub(
        rf"\b(?P<abbr>{_ABBR_ALT})\s+(?P<ch>\d+):(?P<sec>\d+)"
        rf"(?:\.(?P<mom>\d+))?(?:\s*§(?::n)?)?",
        _abbr_chapter,
        text,
    )

    # 5. The same reference with the chapter word SPELLED OUT:
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
    text = re.sub(
        rf"\b(?P<abbr>{_ABBR_ALT})\s+(?P<ch>\d+)\s+{_CHAPTER}\s+"
        rf"(?P<sec>\d+)\s*§(?::n)?(?:\s+(?P<mom>\d+)\s*{_MOM})?",
        _abbr_chapter,
        text,
    )

    # 6. Same shape, law already named in prose ("Maakaaren 2 luvun 1 §").
    #    Requires the chapter word BETWEEN two numbers and a section sign
    #    after, which no ordinary sentence produces by accident.
    text = re.sub(
        rf"\b(?P<ch>\d+)\s+{_CHAPTER}\s+(?P<sec>\d+)\s*§(?::n)?"
        rf"(?:\s+(?P<mom>\d+)\s*{_MOM})?",
        lambda m: (
            f"luku {m.group('ch')} pykälä {m.group('sec')}"
            + (f" momentti {m.group('mom')}" if m.group("mom") else "")
        ),
        text,
    )

    # 7. Law abbreviation + the compact section.moment form: "OikTL 32.1 §"
    #    -> "oikeustoimilain pykälä 32 momentti 1".
    text = re.sub(
        rf"\b(?P<abbr>{_ABBR_ALT})\s+{_SEC_DOT_MOM}",
        lambda m: (
            f"{_genitive(m.group('abbr'))} pykälä {m.group('sec')} "
            f"momentti {m.group('mom')}"
        ),
        text,
    )

    # 8. Law abbreviation + section, possibly with a moment:
    #    "LsL 4 §:n 2 momentti" -> "lastensuojelulain pykälä 4 momentti 2"
    #    "PL 10 §"              -> "perustuslain pykälä 10"
    def _abbr_section(m: re.Match[str]) -> str:
        law = _genitive(m.group("abbr"))
        sec = m.group("sec")
        mom = m.group("mom")
        out = f"{law} pykälä {sec}"
        if mom:
            out += f" momentti {mom}"
        return out

    text = re.sub(
        rf"\b(?P<abbr>{_ABBR_ALT})\s+(?P<sec>\d+)\s*§(?::n)?"
        rf"(?:\s+(?P<mom>\d+)\s*{_MOM})?",
        _abbr_section,
        text,
    )

    # 9. Law abbreviation + article: "EIS 8 art." -> "... artikla 8".
    text = re.sub(
        rf"\b(?P<abbr>{_ABBR_ALT})\s+(?P<art>\d+)\s*{_ART}",
        lambda m: f"{_genitive(m.group('abbr'))} artikla {m.group('art')}",
        text,
    )

    # 10. Law abbreviation directly before a statute number (already "kautta"-ed
    #    in pass 1), typically the parenthetical that defines the abbreviation:
    #    "(LsL 417 kautta 2007)" -> "(lastensuojelulaki 417 kautta 2007)".
    text = re.sub(
        rf"\b(?P<abbr>{_ABBR_ALT})\s+(?=\d+\s+kautta\s+\d{{4}})",
        lambda m: f"{_nominative(m.group('abbr'))} ",
        text,
    )

    # 11. Standalone section.moment, law name already spelled out in prose
    #    ("sovitellaan 36.2 §:n nojalla"). MUST precede pass 5, which would
    #    otherwise match only the "2 §" tail.
    text = re.sub(
        _SEC_DOT_MOM,
        lambda m: f"pykälä {m.group('sec')} momentti {m.group('mom')}",
        text,
    )

    # 12. Standalone section/article (law name already spelled out, e.g.
    #    "Lastensuojelulain 31 §"): convert the symbol after the number.
    text = re.sub(
        rf"\b(?P<sec>\d+)\s*§(?::n)?(?:\s+(?P<mom>\d+)\s*{_MOM})?",
        lambda m: (
            f"pykälä {m.group('sec')}"
            + (f" momentti {m.group('mom')}" if m.group("mom") else "")
        ),
        text,
    )
    text = re.sub(
        rf"\b(?P<art>\d+)\s*{_ART}",
        lambda m: f"artikla {m.group('art')}",
        text,
    )

    # 13. Page ranges: "s. 56-77" / "s. 56–77" -> "sivut 56-77" (plural form;
    #    the existing normalizer otherwise emits singular "sivu" and the range
    #    reads as two stray numbers). A lone "s. 42" stays for Pass E.
    text = re.sub(
        r"\bs\.\s*(\d+)\s*[-–]\s*(\d+)", r"sivut \1-\2", text
    )

    # 14. Drop a redundant law name inside a parenthetical citation when the same
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

    text = re.sub(
        r"(\w+)\s+\((\w+)\s+((?:pykälä|artikla|\d)[^)]*)\)", _dedup, text
    )

    # 15. Same, for an exactly-repeated MULTI-word law name (e.g. "Euroopan
    #    ihmisoikeussopimuksen (Euroopan ihmisoikeussopimuksen artikla 8)").
    text = re.sub(
        r"(\w[\w-]+(?:\s+\w[\w-]+){1,3})\s+\(\1\s+(?=pykälä|artikla|\d)",
        r"\1 (",
        text,
    )

    return text
