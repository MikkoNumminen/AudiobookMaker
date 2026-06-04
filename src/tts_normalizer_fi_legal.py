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
- ``8 art.``      -> ``artikla 8``
- ``417/2007``    -> ``417 kautta 2007``
- ``s. 56-77``    -> ``sivut 56-77``
The cardinal "pykälä N" form avoids Finnish ordinal + case agreement, which
num2words cannot produce reliably.
"""

from __future__ import annotations

import re

# Law abbreviation -> (nominative, genitive) spoken forms. Genitive is used in
# the section/article context ("perustuslain pykälä 10"); nominative in a
# bare statute-number citation ("lastensuojelulaki 417 kautta 2007"). Extend
# this map as new abbreviations show up in source documents.
_LAW_ABBR: dict[str, tuple[str, str]] = {
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
}

_ABBR_ALT = "|".join(sorted(_LAW_ABBR, key=len, reverse=True))

# A "moment" token: "momentti", "momentin", "momenttina", "mom", "mom.".
# Full-word forms MUST precede the "mom" abbreviation, otherwise the regex
# alternation matches "mom" as a prefix of "momentti" and leaves "entti".
_MOM = r"(?:moment\w*|mom\.?)"
# An "article" token, same longest-first rule for "artikla" vs "art".
_ART = r"(?:artikl\w*|art\.?)"


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

    # 2. Law abbreviation + section, possibly with a moment:
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

    # 3. Law abbreviation + article: "EIS 8 art." -> "... artikla 8".
    text = re.sub(
        rf"\b(?P<abbr>{_ABBR_ALT})\s+(?P<art>\d+)\s*{_ART}",
        lambda m: f"{_genitive(m.group('abbr'))} artikla {m.group('art')}",
        text,
    )

    # 4. Law abbreviation directly before a statute number (already "kautta"-ed
    #    in pass 1), typically the parenthetical that defines the abbreviation:
    #    "(LsL 417 kautta 2007)" -> "(lastensuojelulaki 417 kautta 2007)".
    text = re.sub(
        rf"\b(?P<abbr>{_ABBR_ALT})\s+(?=\d+\s+kautta\s+\d{{4}})",
        lambda m: f"{_nominative(m.group('abbr'))} ",
        text,
    )

    # 5. Standalone section/article (law name already spelled out, e.g.
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

    # 6. Page ranges: "s. 56-77" / "s. 56–77" -> "sivut 56-77" (plural form;
    #    the existing normalizer otherwise emits singular "sivu" and the range
    #    reads as two stray numbers). A lone "s. 42" stays for Pass E.
    text = re.sub(
        r"\bs\.\s*(\d+)\s*[-–]\s*(\d+)", r"sivut \1-\2", text
    )

    # 7. Drop a redundant law name inside a parenthetical citation when the same
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

    # 8. Same, for an exactly-repeated MULTI-word law name (e.g. "Euroopan
    #    ihmisoikeussopimuksen (Euroopan ihmisoikeussopimuksen artikla 8)").
    text = re.sub(
        r"(\w[\w-]+(?:\s+\w[\w-]+){1,3})\s+\(\1\s+(?=pykälä|artikla|\d)",
        r"\1 (",
        text,
    )

    return text
