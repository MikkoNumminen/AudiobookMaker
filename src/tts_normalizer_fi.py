"""Finnish text normalizer for TTS.

Extracted from ``src/tts_engine.py`` as part of the engine split. The
normalizer runs a fixed sequence of passes (A, B, C, ..., N) that rewrite
Finnish-specific patterns so any downstream TTS engine reads them
correctly. Entry point: :func:`normalize_finnish_text`.

Pure text in / text out. No audio or synthesis dependencies.
"""

from __future__ import annotations

import re
from typing import Optional

from src.fi_loanwords import apply_loanword_respellings


# ---------------------------------------------------------------------------
# Finnish text normalization
# ---------------------------------------------------------------------------
#
# Raw PDF text often contains Finnish-specific patterns that TTS engines
# read poorly: bare years ("1500"), century expressions ("1500-luvulla"),
# numeric ranges, page abbreviations, decimals, and elided-hyphen compounds
# ("keski-ja" instead of "keski- ja"). Edge-TTS Noora handles some cases
# server-side but not all; Chatterbox-TTS has no number normalization at
# all. Normalizing before chunking benefits every engine we plug in.
#
# Passes run in a fixed order because earlier patterns must consume their
# digits before the generic "bare integer" fallback rewrites them. For
# example, "1500-luvulla" MUST be handled by pass C before pass G sees a
# loose 1500.

# Pass A: bibliographic citations — parens containing a 4-digit year and a
# Capitalized publisher-ish token. Conservative: requires BOTH.
_FI_CITE_RE = re.compile(
    r"\s*\(([^()]*?\b[A-ZÅÄÖ][\wäöåÄÖÅ]+[^()]*?\b\d{4}[a-z]?\b[^()]*?)\)"
)

# Pass A extension — metadata paren drop (ISBN/DOI/CC license/etc.)
_FI_METADATA_PAREN_RE = re.compile(
    r"\s*\([^()]*(?:ISBN|DOI|Creative Commons|CC\s*BY|CC0|CC\s*4\.0|eISBN)[^()]*\)",
    re.IGNORECASE,
)

# Pass J1 — ellipsis collapse.
# Replace 3+ ASCII periods surrounded by whitespace/boundary with Unicode ellipsis.
_FI_ELLIPSIS_RE = re.compile(r"(?<!\S)\.{3,}(?!\S)")

# Pass J2 — TOC dot-leader drop.
# Matches 4+ consecutive dots followed by optional whitespace and a digit.
_FI_TOC_DOT_LEADER_RE = re.compile(r"\s*\.{4,}\s*\d+\b")

# Pass J3 — ISBN strip.
# Matches ISBN-13 with or without prefix, with/without hyphens/spaces.
_FI_ISBN_RE = re.compile(
    r"\b(?:ISBN[\s:-]*)?97[89][- ]?\d{1,5}[- ]?\d{1,7}[- ]?\d{1,7}[- ]?\d\b",
    re.IGNORECASE,
)

# Pass B: elided-hyphen Finnish compounds (e.g. "keski-ja" → "keski- ja").
_FI_ELIDED_HYPHEN_RE = re.compile(
    r"(\w+)-(ja|tai|eli|sekä)\b", re.IGNORECASE
)

# Pass K: Finnish abbreviation expansion.
#
# Expands common Finnish abbreviations to their full spoken forms.
# Must run before Pass C (century expressions) so that abbreviation
# periods do not interfere with period-sensitive downstream patterns.
# All expansions are emitted in lowercase regardless of input case.
#
# The `tri` trigger is special — it has no trailing period and only
# expands before a space + capital letter (a person's name).

_FI_ABBREV_MAP: dict[str, str] = {
    # Reference abbreviations (sorted longest-first for regex alternation)
    "yms.": "ynnä muuta sellaista",
    "tms.": "tai muuta sellaista",
    "jne.": "ja niin edelleen",
    "vrt.": "vertaa",
    "huom.": "huomaa",
    "esim.": "esimerkiksi",
    "ts.": "toisin sanoen",
    "ks.": "katso",
    "ym.": "ynnä muuta",
    "mm.": "muun muassa",
    "nk.": "niin kutsuttu",
    "ns.": "niin sanottu",
    "ko.": "kyseinen",
    "ao.": "asianomainen",
    "ed.": "edellinen",
    # Era abbreviations
    "eKr.": "ennen Kristusta",
    "jKr.": "jälkeen Kristuksen",
    "eaa.": "ennen ajanlaskun alkua",
    "jaa.": "jälkeen ajanlaskun alun",
    # Titles
    "prof.": "professori",
    "dos.": "dosentti",
    "fil.": "filosofian",
    "maist.": "maisteri",
    "kand.": "kandidaatti",
    "toim.": "toimittaja",
    # Count / quantity
    "kpl.": "kappaletta",
    "milj.": "miljoonaa",
    "mrd.": "miljardia",
}

# Build one regex alternation sorted longest-first to prevent partial matches.
_FI_ABBREV_RE = re.compile(
    r"\b(" + "|".join(
        re.escape(k) for k in sorted(_FI_ABBREV_MAP, key=len, reverse=True)
    ) + r")",
    re.IGNORECASE,
)

# `tri` without period: only expand when followed by space + capital letter
# (i.e. a person's name). Word boundary at start; lookahead for the name.
_FI_TRI_RE = re.compile(r"\btri(?=\s+[A-ZÄÖÅ])")


def _expand_abbreviations(text: str) -> str:
    """Expand Finnish abbreviations to their full spoken forms (Pass K).

    Uses a case-insensitive match and always emits the lowercase expansion.
    The special case `tri` (without a period) is expanded to `tohtori` only
    when immediately followed by a space and a capital letter (a person's name).
    """
    def _abbrev_sub(m: re.Match) -> str:
        key = m.group(1).lower()
        # eKr. and jKr. have mixed case keys — look up both variants
        expansion = _FI_ABBREV_MAP.get(key)
        if expansion is None:
            # Try original capitalisation (for eKr. / jKr.)
            expansion = _FI_ABBREV_MAP.get(m.group(1))
        return expansion if expansion is not None else m.group(1)

    text = _FI_ABBREV_RE.sub(_abbrev_sub, text)
    text = _FI_TRI_RE.sub("tohtori", text)
    return text


# Pass N: Finnish acronym expansion (known whitelist).
#
# Expands a fixed set of acronyms to their Finnish spoken forms.
# Matching is exact-case and word-boundary anchored so that:
#   - lowercase `eu` (negative prefix) is NOT expanded
#   - inflected forms like `NATOn` or `EU:n` may or may not match depending
#     on whether the boundary falls (see _expand_acronyms docstring)
#   - unknown ALL-CAPS tokens are left unchanged (no heuristic fallback)
#
# Run AFTER Pass K (abbreviations) and BEFORE Pass M (units).

_FI_ACRONYM_LOOKUP: dict[str, str] = {
    # Political / international organizations
    "EU": "Euroopan unioni",
    "EY": "Euroopan yhteisö",
    "EEC": "Euroopan talousyhteisö",
    "YK": "Yhdistyneet kansakunnat",
    "USA": "Yhdysvallat",
    "NATO": "Nato",  # Read as a word, not letter-by-letter
    "UNESCO": "Unesco",
    "WTO": "W T O",  # Letter-by-letter
    "ILO": "I L O",
    "UN": "U N",
    # German-language legal acronyms (common in Finnish legal history)
    "ALR": "A L R",  # Allgemeines Landrecht
    "ABGB": "A B G B",  # Österreichisches Allgemeines Bürgerliches Gesetzbuch
    "BGB": "B G B",  # Bürgerliches Gesetzbuch
    "HGB": "H G B",  # Handelsgesetzbuch
    "StGB": "St G B",  # Strafgesetzbuch
    "StPO": "St P O",  # Strafprozessordnung
    "ZPO": "Z P O",  # Zivilprozessordnung
    # Finnish legal acronyms
    "RL": "R L",  # Rikoslaki
    "SL": "S L",  # Siviililaki
    # Common modern
    "PDF": "P D F",
    "URL": "U R L",
    "API": "A P I",
}

# Build a regex alternation sorted longest-first so `ABGB` matches before
# `BGB`, etc. Case-sensitive: `EU` ≠ `eu` (negative prefix in Finnish).
_FI_ACRONYM_RE = re.compile(
    r"\b(" + "|".join(
        re.escape(k) for k in sorted(_FI_ACRONYM_LOOKUP, key=len, reverse=True)
    ) + r")\b"
)


def _expand_acronyms(text: str) -> str:
    """Expand known Finnish/legal acronyms to their spoken forms (Pass N).

    Matching is exact-case and word-boundary anchored (\\b). Consequences:
    - ``EU:n`` — the colon is a non-word character so ``\\b`` fires between
      ``EU`` and ``:``, meaning ``EU`` IS matched and expanded.
    - ``NATOn`` — ``O`` and ``n`` are both word characters so no ``\\b``
      boundary exists inside the token; ``NATO`` is NOT matched.
    - ``ABGB-laki`` — the hyphen is a non-word character so ``\\b`` fires
      between ``ABGB`` and ``-``; ``ABGB`` IS matched and expanded.
    - Unknown all-caps tokens are left unchanged (no heuristic fallback).
    """
    def _sub(m: re.Match) -> str:
        return _FI_ACRONYM_LOOKUP[m.group(1)]
    return _FI_ACRONYM_RE.sub(_sub, text)


# Pass M: measurement unit / currency symbol expansion.
#
# Replaces numeric-prefixed unit symbols with their Finnish partitive forms
# so Pass G's governor detection sees `5 prosenttia` and picks nominative
# from `_FI_GOVERNOR_AFTER["prosenttia"]`.  Must run before Pass C/D/F/G.
#
# Units are ordered longest-first in the alternation to avoid partial matches
# (e.g. `°C` before bare `C`, `km` before bare `m`).

_FI_UNIT_MAP: list[tuple[str, str]] = [
    # Temperature (allow optional negative sign on the digit)
    ("°C", "celsiusastetta"),
    ("°F", "fahrenheitastetta"),
    # Length (multi-char first)
    ("km", "kilometriä"),
    ("cm", "senttimetriä"),
    ("mm", "millimetriä"),
    ("m", "metriä"),
    # Mass (multi-char first)
    ("kg", "kilogrammaa"),
    ("mg", "milligrammaa"),
    ("g", "grammaa"),
    ("t", "tonnia"),
    # Volume (multi-char first)
    ("ml", "millilitraa"),
    ("dl", "desilitraa"),
    ("cl", "senttilitraa"),
    ("l", "litraa"),
    # Time
    ("min", "minuuttia"),
    # Currency (suffix — after number)
    ("€", "euroa"),
    ("£", "puntaa"),
    # Percent / per-mille
    ("%", "prosenttia"),
    ("‰", "promillea"),
    # Legacy Finnish currency
    ("mk", "markkaa"),
]

# Build a single regex: (-?\d+(?:[.,]\d+)?)\s*(<unit>) followed by a
# negative lookahead for word characters so word-character units like `km`
# are not greedily matched mid-word, while symbol units like `%` and `€`
# (which are non-word chars and don't support `\b`) still match correctly.
# Units are sorted longest-first.
_FI_UNIT_RE = re.compile(
    r"(-?\d+(?:[.,]\d+)?)\s*("
    + "|".join(
        re.escape(sym) for sym, _ in _FI_UNIT_MAP
    )
    + r")(?!\w)",
)

# Dollar sign precedes the number: $5 → 5 dollaria
_FI_DOLLAR_RE = re.compile(r"\$\s*(\d+(?:[.,]\d+)?)")

# Section sign (§) precedes the number: § 242 → pykälä 242. The output
# `pykälä` then acts as a before-governor for Pass G, which emits the
# number in nominative by default. In the rare case the surrounding
# prose already contains `pykälässä` or similar, both governors are
# visible to Pass G's ±3-word scan; the nearer one wins (typically the
# inserted `pykälä` at distance 1).
_FI_SECTION_SIGN_RE = re.compile(r"§\s*(?=\d)")

# Lookup dict for unit expansion (symbol → Finnish word).
_FI_UNIT_LOOKUP: dict[str, str] = {sym: word for sym, word in _FI_UNIT_MAP}


def _expand_unit_symbols(text: str) -> str:
    """Expand numeric unit symbols to Finnish partitive forms (Pass M).

    Keeps the digit token in place so Pass G's governor table can still
    detect the unit word and assign nominative case to the numeral.
    """
    # Dollar sign: prefix form — convert first.
    text = _FI_DOLLAR_RE.sub(r"\1 dollaria", text)

    # Section sign (§): prefix form — `§ 242` → `pykälä 242`.
    text = _FI_SECTION_SIGN_RE.sub("pykälä ", text)

    def _unit_sub(m: re.Match) -> str:
        number = m.group(1)
        sym = m.group(2)
        word = _FI_UNIT_LOOKUP.get(sym, sym)
        return f"{number} {word}"

    return _FI_UNIT_RE.sub(_unit_sub, text)


# Pass C: century/era expressions — digit + "-luku" declension suffix.
_FI_CENTURY_SUFFIXES = (
    "luvulla",
    "luvulta",
    "luvulle",
    "luvuilla",
    "luvusta",
    "luvut",
    "luvun",
    "luku",
)
_FI_CENTURY_RE = re.compile(
    r"(\d+)-(" + "|".join(_FI_CENTURY_SUFFIXES) + r")\b"
)

# Pass D: numeric ranges. Matches 1–4 digit numbers on both sides of
# a hyphen or en-dash so short ranges like `sivuilta 42-45` also get
# their endpoints inflected by Pass G's governor detection. The dash
# is replaced with a space so Pass G's tokenizer sees two separate
# `num` tokens and each one independently looks up ±3 word tokens for
# a governor. Ranges without a governor (bare `5-2` arithmetic) fall
# back to nominative on both endpoints, which is acceptable for TTS.
_FI_RANGE_RE = re.compile(r"(\d{1,4})\s*[-–]\s*(\d{1,4})\b")

# Pass E: "s. 42" / "ss. 42-45" page abbreviation. Expand ONLY the
# abbreviation; leave the digits for Pass G so governor-aware case
# detection can inflect the number via `sivu`/`sivut` context.
_FI_PAGE_RE = re.compile(r"\bs(s?)\.\s+(?=\d)")

# Pass F: decimals (comma or dot separator).
_FI_DECIMAL_RE = re.compile(r"(\d+)[.,](\d+)")

# Pass G: tokenizer for governor-aware integer expansion. Matches either
# a digit run (number), a word (letters incl. Finnish diacritics), or a
# single non-space character (punctuation). We walk the tokens and look
# ±3 WORD tokens around each number for a grammatical governor.
_FI_TOKEN_RE = re.compile(
    r"(?P<num>\d+)|(?P<word>[^\W\d_]+)|(?P<other>[^\s])",
    re.UNICODE,
)

# Governor-word → num2words case. "Before" governors sit left of the
# number (`vuonna 1905`, `sivulta 42`); "after" governors sit right
# (`5 prosenttia`, `3 kertaa`). All keys are lowercase. The table is the
# machine-readable form of `docs/finnish_governor_cases.md` and
# `docs/finnish_normalizer_design.md` §1. Add new entries by lemma, not
# by surface form — morphology is handled by listing each case form we
# care about as its own key.
_FI_GOVERNOR_BEFORE: dict[str, str] = {
    # Year governors. In the default `year_shortening="radio"` mode
    # these are *overridden* to nominative for 4-digit year literals
    # (1000–2100) to match the Kielikello radio convention. Set
    # `year_shortening="full"` in TTSConfig to honor the table below.
    "vuonna": "nominative",
    "vuoden": "nominative",
    "vuodelta": "ablative",
    "vuoteen": "illative",
    "vuodesta": "elative",
    "vuosina": "essive",
    # Page governors — singular.
    "sivu": "nominative",
    "sivut": "nominative",
    "sivulla": "adessive",
    "sivulta": "ablative",
    "sivulle": "allative",
    "sivusta": "elative",
    "sivuun": "illative",
    # Page governors — plural (e.g. `sivuilta 42-45` → ablative both).
    "sivuilla": "adessive",
    "sivuilta": "ablative",
    "sivuille": "allative",
    "sivuista": "elative",
    "sivuihin": "illative",
    # Chapter / section / paragraph governors.
    "luku": "nominative",
    "luvussa": "inessive",
    "lukuun": "illative",
    "luvun": "genitive",
    "luvusta": "elative",
    "luvulla": "adessive",
    "pykälä": "nominative",
    "pykälässä": "inessive",
    "pykälästä": "elative",
    "pykälään": "illative",
    "kappale": "nominative",
    "kappaleessa": "inessive",
    "kappaleesta": "elative",
    "osa": "nominative",
    "osassa": "inessive",
    "osasta": "elative",
    "kohta": "nominative",
    "kohdassa": "inessive",
    "kohdasta": "elative",
    "kohtaan": "illative",
    "kohdalla": "adessive",
    # Row / line positions — singular.
    "rivi": "nominative",
    "rivillä": "adessive",
    "riviltä": "ablative",
    "riville": "allative",
    # Row / line positions — plural.
    "riveillä": "adessive",
    "riveiltä": "ablative",
    "riveille": "allative",
    # Clock-time: `klo` / `kello` are frozen adverbials; the hour itself
    # stays nominative. See Q2 in docs/finnish_governor_cases.md.
    "klo": "nominative",
    "kello": "nominative",
}

_FI_GOVERNOR_AFTER: dict[str, str] = {
    # Partitive head nouns. By VISK §772 and Kielikello "viisi kertaa",
    # the numeral stays NOMINATIVE — the head noun carries the partitive
    # on its own. We still record these here so the detector recognizes
    # the construction and does not fall through to some earlier
    # before-governor in the same window.
    "prosenttia": "nominative",
    "prosentin": "nominative",
    "promillea": "nominative",
    "kertaa": "nominative",
    "kerran": "nominative",
    "kappaletta": "nominative",
    "kpl": "nominative",
    "vuotta": "nominative",
    "kuukautta": "nominative",
    "viikkoa": "nominative",
    "päivää": "nominative",
    "tuntia": "nominative",
    "minuuttia": "nominative",
    "sekuntia": "nominative",
    "metriä": "nominative",
    "kilometriä": "nominative",
    "senttiä": "nominative",
    "senttimetriä": "nominative",
    "millimetriä": "nominative",
    "grammaa": "nominative",
    "kiloa": "nominative",
    "kilogrammaa": "nominative",
    "euroa": "nominative",
    "senttiä": "nominative",
    "markkaa": "nominative",
    # Added for Pass M unit expansion
    "milligrammaa": "nominative",
    "millilitraa": "nominative",
    "desilitraa": "nominative",
    "senttilitraa": "nominative",
    "litraa": "nominative",
    "tonnia": "nominative",
    "celsiusastetta": "nominative",
    "fahrenheitastetta": "nominative",
    "dollaria": "nominative",
    "puntaa": "nominative",
    "miljoonaa": "nominative",
    "miljardia": "nominative",
}

# Year governors — trigger last-part / radio-convention handling when
# `year_shortening == "radio"` (default) and the bare integer looks like
# a 4-digit year.
_FI_YEAR_GOVERNORS = frozenset({
    "vuonna", "vuoden", "vuodelta", "vuoteen", "vuodesta", "vuosina",
})

# Pass L: Roman numeral expansion.
#
# Matches sequences of 2+ Roman-numeral capital letters at word boundaries.
# The lookahead `(?=[IVXLCDM]{2,}\b)` excludes standalone `I`, `V`, `X`, etc.
# so the English pronoun "I" and single-letter abbreviations are never touched.
_FI_ROMAN_RE = re.compile(
    r"\b(?=[IVXLCDM]{2,}\b)(M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))\b"
)

# Tokens that are modern acronyms but happen to be valid Roman numerals.
# Case-sensitive — matched against the raw (upper-cased) token.
_FI_ROMAN_BLACKLIST = frozenset({
    "DC",   # direct current
    "LCD",  # liquid crystal display
    "MVP",  # most valuable player
    "CV",   # curriculum vitae
    "CI",   # continuous integration / confidence interval
    "MD",   # doctor of medicine
    "ID",   # identification
})

# Regnal first names and ecclesial / political titles that, when they
# immediately precede a Roman numeral, make it an ordinal (e.g. "Kustaa II").
_FI_ROMAN_REGNAL_NAMES = frozenset({
    "Kustaa", "Kaarle", "Juhana", "Eerik", "Henrik", "Pius", "Leo",
    "Aleksanteri", "Nikolai", "Katariina", "Elisabet", "Yrjö",
    "Fredrik", "Adolf", "Oskar", "Erik",
})
_FI_ROMAN_TITLES = frozenset({
    "paavi", "Paavi", "kuningas", "Kuningas", "keisari", "Keisari",
    "tsaari", "Tsaari", "sulttaani", "Sulttaani",
})

# Words that follow a Roman numeral and signal ordinal reading
# (e.g. "XIX vuosisata" → "yhdeksästoista vuosisata").
_FI_ROMAN_ORDINAL_AFTER = frozenset({
    "vuosisata", "vuosisadalla", "vuosisadalta", "vuosisatana",
    "luku", "luvulla", "luvulta", "luvussa", "luvun",
    "kappale", "kappaleessa", "kappaletta",
})

# Words that precede a Roman numeral and signal ordinal reading
# (e.g. "luku IV" → "luku neljäs").
_FI_ROMAN_ORDINAL_BEFORE = frozenset({
    "luku", "Luku", "luvussa", "luvun", "pykälä", "Pykälä",
    "pykälässä", "kohta", "kohdassa",
})


def _roman_to_int(s: str) -> Optional[int]:
    """Convert a Roman numeral string to an integer, or None if invalid.

    Uses the standard subtractive algorithm. Returns None for the empty
    string (which the regex may produce for a zero-valued match) or for
    sequences that are not canonical standard Roman numerals (e.g. IIII).
    Validation: round-trip the result back through a canonical encoder and
    compare — if they differ, the input was non-canonical.
    """
    if not s:
        return None
    val_map = {"I": 1, "V": 5, "X": 10, "L": 50,
               "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(s):
        v = val_map.get(ch)
        if v is None:
            return None
        if v < prev:
            total -= v
        else:
            total += v
        prev = v
    if total <= 0:
        return None
    # Validate canonicity: re-encode and compare.
    n = total
    parts: list[str] = []
    for arabic, roman in (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ):
        while n >= arabic:
            parts.append(roman)
            n -= arabic
    canonical = "".join(parts)
    return total if canonical == s else None


def _expand_roman_numerals(text: str) -> str:
    """Expand Roman numerals to Finnish spoken forms (Pass L).

    For each Roman numeral token (2+ letters, word-boundary anchored):
    - Skip blacklisted acronyms (DC, LCD, CV, etc.).
    - Classify as ORDINAL when preceded by a regnal name/title or
      section keyword, or followed by century/chapter keywords.
    - Fall back to CARDINAL in all other cases.
    - If _roman_to_int returns None (invalid/non-canonical), leave unchanged.
    """
    try:
        from num2words import num2words  # type: ignore
    except ImportError:
        return text

    # Tokenize once to allow ±2 word-token look-around.
    tokens: list[tuple[str, str, int, int]] = []
    for m in _FI_TOKEN_RE.finditer(text):
        if m.group("num") is not None:
            kind = "num"
        elif m.group("word") is not None:
            kind = "word"
        else:
            kind = "other"
        tokens.append((kind, m.group(0), m.start(), m.end()))

    # Map character position → token index for fast lookup.
    pos_to_tok: dict[int, int] = {tok[2]: i for i, tok in enumerate(tokens)}

    def _nearby_words(tok_idx: int, step: int, limit: int = 2) -> list[str]:
        """Return up to `limit` word-token values in direction `step`."""
        result: list[str] = []
        j = tok_idx + step
        while 0 <= j < len(tokens) and len(result) < limit:
            if tokens[j][0] == "word":
                result.append(tokens[j][1])
            j += step
        return result

    parts: list[str] = []
    cursor = 0

    for m in _FI_ROMAN_RE.finditer(text):
        token_str = m.group(0)
        # Blacklist check (case-sensitive on upper form).
        if token_str.upper() in _FI_ROMAN_BLACKLIST:
            continue
        n = _roman_to_int(token_str)
        if n is None:
            continue

        # Find token index for context look-around.
        tok_idx = pos_to_tok.get(m.start())
        is_ordinal = False
        if tok_idx is not None:
            before_words = _nearby_words(tok_idx, -1)
            after_words = _nearby_words(tok_idx, +1)
            # Regnal name or title immediately before → ordinal.
            if before_words and (
                before_words[0] in _FI_ROMAN_REGNAL_NAMES
                or before_words[0] in _FI_ROMAN_TITLES
            ):
                is_ordinal = True
            # Section keyword before → ordinal.
            elif before_words and before_words[0] in _FI_ROMAN_ORDINAL_BEFORE:
                is_ordinal = True
            # Century/chapter keyword after → ordinal.
            elif after_words and after_words[0] in _FI_ROMAN_ORDINAL_AFTER:
                is_ordinal = True

        if is_ordinal:
            try:
                replacement = num2words(n, lang="fi", to="ordinal")
            except (NotImplementedError, OverflowError, ValueError, TypeError):
                replacement = num2words(n, lang="fi")
        else:
            replacement = num2words(n, lang="fi")

        parts.append(text[cursor:m.start()])
        parts.append(replacement)
        cursor = m.end()

    parts.append(text[cursor:])
    return "".join(parts)


# Pass H: split glued Finnish compound-number morphemes.
#
# num2words 0.5.14 emits Finnish compound numbers with the hundreds and
# tens morphemes glued to whatever follows — e.g. 1889 nominative
# "tuhat kahdeksansataakahdeksankymmentäyhdeksän". Chatterbox-TTS then
# tokenizes the glued word as one giant token and mispronounces it.
# We insert a space after every case-inflected form of `sata` and
# `kymmenen` when another morpheme is glued on. Standalone teens like
# "viisitoista" (15) are unaffected — they do not contain these stems.
#
# ORDERING MATTERS. The alternation must try partitive forms
# (`sataa`, `kymmentä`) BEFORE illative forms (`sataan`,
# `kymmeneen`) because inside a nominative compound number like
# `kaksisataaneljäkymmentäkaksi` the literal substring `sataan`
# appears (the `n` belongs to the following `neljäkymmentä`
# morpheme). If the regex tried `sataan` first it would steal that
# `n` and emit `kaksisataan eljäkymmentäkaksi` — clearly wrong.
# Trying `sataa` first matches and splits correctly. Do NOT resort
# by length here; the order below is the correctness guarantee.
_FI_MORPHEME_STEMS: tuple[str, ...] = (
    # Partitive (base) — most common in compound numbers, must win.
    "sataa",
    "kymmentä",
    # Genitive.
    "sadan",
    "kymmenen",
    # Inessive.
    "sadassa",
    "kymmenessä",
    # Elative.
    "sadasta",
    "kymmenestä",
    # Adessive.
    "sadalla",
    "kymmenellä",
    # Ablative.
    "sadalta",
    "kymmeneltä",
    # Allative.
    "sadalle",
    "kymmenelle",
    # Essive.
    "satana",
    "kymmenenä",
    # Translative.
    "sadaksi",
    "kymmeneksi",
    # Plural partitive.
    "satoja",
    # Illative forms LAST — risky inside compound numbers (see above).
    "sataan",
    "kymmeneen",
)
# Restrict the split to cases where a digit (1-9) or a further tens
# stem (`kymmen*`) actually follows. Any-letter lookahead was too
# permissive — it would match e.g. the malformed ordinal
# `viidenkymmenennen` and split off the trailing `nen` as a spurious
# token (`viidenkymmenen nen`), which then gets mispronounced.
# Finnish digit forms (nominative, genitive, partitive, and oblique
# cases 1-9) all start with one of these three-letter prefixes. A
# bare three-letter token that is NOT a digit stem (`nen`, `sta`,
# etc.) will fail the lookahead and leave the compound untouched.
_FI_COMPOUND_DIGIT_PREFIXES: tuple[str, ...] = (
    "yks",  # yksi (1)
    "yhd",  # yhden (1 gen), yhdeksän (9), yhdeksää (9 part)
    "yht",  # yhtä (1 part)
    "kak",  # kaksi (2)
    "kah",  # kahden (2 gen), kahta (2 part), kahdeksan (8)
    "kol",  # kolme (3)
    "nel",  # neljä (4), neljän (4 gen), neljää (4 part)
    "vii",  # viisi (5), viiden (5 gen), viittä (5 part)
    "kuu",  # kuusi (6), kuuden (6 gen), kuutta (6 part)
    "sei",  # seitsemän (7), seitsemää (7 part)
    "kym",  # middle-tens kymmen* (e.g. sadankahden|kymmenen|viiden)
)
_FI_MORPHEME_BOUNDARY_RE = re.compile(
    r"(" + "|".join(_FI_MORPHEME_STEMS) + r")"
    r"(?=(?:" + "|".join(_FI_COMPOUND_DIGIT_PREFIXES) + r"))"
)


def _fi_split_number_compounds(text: str) -> str:
    """Insert spaces at morpheme boundaries in Finnish compound numbers.

    See :data:`_FI_MORPHEME_BOUNDARY_RE` for the rationale. Operates on
    already-normalized text (post num2words expansion).
    """
    return _FI_MORPHEME_BOUNDARY_RE.sub(r"\1 ", text)


def _fi_detect_case(
    tokens: list[tuple[str, str, int, int]],
    idx: int,
    n: int,
    year_shortening: str,
) -> str:
    """Return the num2words `case=` kwarg for the number at token `idx`.

    Walks up to 3 WORD tokens in each direction looking for a governor.
    Nearest governor wins; "before" governors are preferred over "after"
    governors because Finnish attributive structures usually place the
    case-demanding head to the left (`vuonna 1905`, `sivulta 42`).

    `year_shortening` controls the radio-style shortening convention:
    when set to "radio" (default) and `n` is a plausible 4-digit year
    (1000–2100) governed by a year lemma, the return case is forced to
    `"nominative"` regardless of what the governor would normally
    demand. Set to "full" to honor the full case-agreement table.
    """
    def _word_iter(start: int, step: int):
        j = start
        count = 0
        while 0 <= j < len(tokens) and count < 3:
            kind, value, _, _ = tokens[j]
            if kind == "word":
                yield value.lower()
                count += 1
            j += step

    is_year = 1000 <= n <= 2100

    # Nearest "before" governor wins.
    for lemma in _word_iter(idx - 1, -1):
        if lemma in _FI_GOVERNOR_BEFORE:
            if year_shortening == "radio" and is_year \
                    and lemma in _FI_YEAR_GOVERNORS:
                return "nominative"
            return _FI_GOVERNOR_BEFORE[lemma]

    # Otherwise look for a partitive-head "after" governor.
    for lemma in _word_iter(idx + 1, 1):
        if lemma in _FI_GOVERNOR_AFTER:
            return _FI_GOVERNOR_AFTER[lemma]

    return "nominative"


_MY_LANG = "fi"


def normalize_finnish_text(
    text: str,
    drop_citations: bool = True,
    year_shortening: str = "radio",
    *,
    _lang: str | None = None,
) -> str:
    """Expand Finnish-specific patterns so TTS engines read them correctly.

    Rewrites numbers, century expressions, numeric ranges, page abbreviations,
    and elided-hyphen compounds into plain word-form Finnish. Uses num2words
    (lazy import) for the actual digit → word conversion with
    ``case=`` set from governor-word detection (±3 word tokens of
    context). If num2words is not installed the function degrades
    gracefully and returns the input unchanged.

    Also applies Pass L (Roman numeral expansion) which converts Roman
    numerals to Finnish spoken forms with context-aware ordinal vs. cardinal
    selection (e.g. ``Kustaa II`` → ``Kustaa toinen``,
    ``XIX vuosisata`` → ``yhdeksästoista vuosisata``).
    Also applies Pass N (acronym expansion) which replaces a fixed whitelist
    of known acronyms (``EU``, ``YK``, ``NATO``, legal codes, etc.) with
    their Finnish spoken forms before unit/number processing.

    Also applies Pass I (Finnish loanword respelling) which fixes common
    mispronunciations of loanword suffixes like ``-ismi`` and ``-tio`` by
    inserting hyphens that guide the TTS engine's pronunciation
    (e.g. ``humanismi`` → ``humanis-mi``, ``instituutio`` → ``instituu-tio``).

    Args:
        text: Raw Finnish text.
        drop_citations: If True, strip bibliographic citations like
            "(Pihlajamäki 2005)" — they are distracting when read aloud.
        year_shortening: ``"radio"`` (default) follows the Kielikello
            radio convention where 4-digit years are read in nominative
            regardless of the governing year preposition
            (`vuodesta 1917` → "vuodesta tuhat yhdeksänsataa
            seitsemäntoista"). ``"full"`` emits the full case agreement
            (`vuodesta 1917` → "vuodesta tuhannesta
            yhdeksästäsadastaseitsemästätoista"). Only affects year
            literals in the 1000–2100 range; other integers always
            follow the governor table.

    Returns:
        Normalized text ready for TTS synthesis.
    """
    if _lang is not None and _lang != _MY_LANG:
        from src.tts_normalizer import LanguageMismatchError
        raise LanguageMismatchError(
            f"normalize_finnish_text called with _lang={_lang!r}; "
            f"this module only handles {_MY_LANG!r}. "
            f"Use src.tts_normalizer.normalize_text instead."
        )
    if not text:
        return text
    try:
        from num2words import num2words  # type: ignore
    except ImportError:
        return text

    def _w(n: int, case: str = "nominative") -> str:
        try:
            return num2words(n, lang="fi", case=case)
        except (NotImplementedError, OverflowError, ValueError, TypeError):
            return str(n)

    # Pass A — drop bibliographic citations and metadata parens.
    if drop_citations:
        text = _FI_CITE_RE.sub("", text)
        text = _FI_METADATA_PAREN_RE.sub("", text)

    # Pass J1 — ellipsis collapse (3+ dots surrounded by whitespace → …).
    text = _FI_ELLIPSIS_RE.sub("…", text)

    # Pass J2 — TOC dot-leader drop (4+ dots followed by page number).
    text = _FI_TOC_DOT_LEADER_RE.sub(" ", text)

    # Pass J3 — ISBN strip (bare ISBN-13 numbers in prose).
    text = _FI_ISBN_RE.sub("", text)

    # Pass B — elided-hyphen compounds (just insert a space).
    text = _FI_ELIDED_HYPHEN_RE.sub(r"\1- \2", text)

    # Pass K — Finnish abbreviation expansion. Must run before Pass C so
    # abbreviation periods do not interfere with period-sensitive patterns.
    text = _expand_abbreviations(text)

    # Pass L — Roman numerals (regnal ordinals, chapter ordinals, cardinal fallback).
    # Runs after Pass K (abbreviation expansion) so periods in abbreviations
    # don't bleed into Roman numeral detection; before Pass M so unit expansion
    # doesn't consume context the ordinal classifier needs.
    text = _expand_roman_numerals(text)
    # Pass N — acronym expansion (known whitelist, exact-case).
    text = _expand_acronyms(text)

    # Pass M — measurement unit / currency symbol expansion. Must run
    # before Pass D/F/G so the digit prefix stays intact for governor
    # detection (e.g. `5 prosenttia` → Pass G picks nominative).
    text = _expand_unit_symbols(text)

    # Pass C — century expressions.
    def _century_sub(m: re.Match) -> str:
        return f"{_w(int(m.group(1)))} {m.group(2)}"

    text = _FI_CENTURY_RE.sub(_century_sub, text)

    # Pass D — numeric ranges. Split the endpoints on the dash and let
    # Pass G's tokenizer + governor detection handle each endpoint
    # independently. `vuosina 1914-1918` under year_shortening="full"
    # inflects both endpoints in essive; under "radio" both stay
    # nominative. The regex only matches 3-4 digit ranges to avoid
    # collision with short math expressions (a session 2 concern).
    text = _FI_RANGE_RE.sub(r"\1 \2", text)

    # Pass E — abbreviation expansion only. "s. 42" → "sivu 42",
    # "ss. 42-45" → "sivut 42-45". The digit is left for Pass G so
    # governor-aware case inflection picks up `sivu` / `sivut`.
    def _page_sub(m: re.Match) -> str:
        return "sivut " if m.group(1) else "sivu "

    text = _FI_PAGE_RE.sub(_page_sub, text)

    # Pass F — decimals. Decimals rarely participate in
    # governor-cased constructions in prose, so we keep them simple
    # (nominative float expansion).
    def _decimal_sub(m: re.Match) -> str:
        whole = int(m.group(1))
        frac_str = m.group(2)
        try:
            return num2words(float(f"{whole}.{frac_str}"), lang="fi")
        except (NotImplementedError, ValueError):
            return f"{_w(whole)} pilkku {' '.join(_w(int(d)) for d in frac_str)}"

    text = _FI_DECIMAL_RE.sub(_decimal_sub, text)

    # Pass G — governor-aware integer expansion. Tokenize the text,
    # walk the tokens, and for every bare integer detect the governing
    # word within ±3 word tokens to pick the correct num2words case.
    tokens: list[tuple[str, str, int, int]] = []
    for m in _FI_TOKEN_RE.finditer(text):
        if m.group("num") is not None:
            kind = "num"
        elif m.group("word") is not None:
            kind = "word"
        else:
            kind = "other"
        tokens.append((kind, m.group(0), m.start(), m.end()))

    parts: list[str] = []
    cursor = 0
    for i, (kind, value, start, end) in enumerate(tokens):
        if kind != "num":
            continue
        n = int(value)
        case = _fi_detect_case(tokens, i, n, year_shortening)
        parts.append(text[cursor:start])
        parts.append(_w(n, case))
        cursor = end
    parts.append(text[cursor:])
    text = "".join(parts)

    # Pass I — Finnish loanword respelling (post num2words, pre morpheme split).
    # Fixes mispronunciations of loanword suffixes (-ismi, -tio) and substitutes
    # foreign names / Latin phrases with phonetically correct Finnish spellings.
    # Loanwords contain no digits so num2words cannot collide; running before
    # Pass H prevents double-splitting a respelled form that already has a hyphen.
    text = apply_loanword_respellings(text)

    # Pass H — split glued compound-number morphemes (post num2words).
    text = _fi_split_number_compounds(text)

    # Collapse whitespace introduced by deletions/substitutions.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([.,;:!?])", r"\1", text)
    return text
