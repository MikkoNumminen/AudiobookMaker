"""English text normalizer for TTS audiobook synthesis.

Phase 1 passes (A–K):
    A. Metadata strip — ISBN, DOI, copyright/CC license parens
    B. Whitespace/quote cleanup — smart quotes, NBSP, ellipsis, TOC dot leaders
    C. Abbreviations — Mr./Dr./St./vs./etc./i.e./e.g./a.m./p.m./No./Vol./Ch./pp./p.
    D. Roman numerals in context — Chapter IV, Louis XIV (whitelist-guarded)
    E. Ordinal digit forms — 1st, 2nd, 21st via num2words
    F. Years — 1917 → "nineteen seventeen", 1920s → "nineteen twenties"
    G. Cardinal integers — bare numbers via num2words
    H. Decimals — 3.14 → "three point one four"
    I. Fractions — 1/2 → "one half", 3/4 → "three quarters"
    J. Sentence-terminal period normalisation
    K. Final whitespace collapse

Design notes
------------
- The rules are inspired by NVIDIA NeMo's English text-processing
  grammars (Apache 2.0, https://github.com/NVIDIA/NeMo-text-processing).
  Reimplemented in plain Python — no `pynini`, no FST runtime, no NeMo
  dependency.
- The only external dep is `num2words`, which is already required by the
  Finnish normalizer. If `num2words` is missing the function returns
  text unchanged (graceful degradation, mirroring `tts_normalizer_fi`).
- Phase 2 will add currency, units, dates, telephone, URLs, acronyms.
- The `_lang` kwarg + LanguageMismatchError pattern matches
  `tts_normalizer_fi` exactly so the dispatcher can guard both modules
  identically.
"""

from __future__ import annotations

import re

_MY_LANG = "en"


# ---------------------------------------------------------------------------
# Pass A — metadata strip
# ---------------------------------------------------------------------------

# Bare ISBN-13 / ISBN-10 patterns frequently embedded in front matter.
_EN_ISBN_RE = re.compile(
    r"\bISBN(?:-1[03])?:?\s*[\d\-Xx ]{10,17}\b"
)

# DOI patterns: "doi:10.xxxx/..." or "https://doi.org/..."
_EN_DOI_RE = re.compile(
    r"\b(?:doi:|https?://(?:dx\.)?doi\.org/)[\w./()-]+",
    flags=re.IGNORECASE,
)

# Copyright / Creative Commons / All rights reserved noise lines.
_EN_COPYRIGHT_RE = re.compile(
    r"©\s*\d{4}[^.\n]*",
)
_EN_CC_LICENSE_RE = re.compile(
    r"\bCC[ -]BY(?:-[A-Z]{2})?(?:[ -]\d(?:\.\d)?)?\b",
    flags=re.IGNORECASE,
)
_EN_ALL_RIGHTS_RE = re.compile(
    r"\bAll rights reserved\.?",
    flags=re.IGNORECASE,
)


def _pass_a_metadata_strip(text: str) -> str:
    text = _EN_ISBN_RE.sub("", text)
    text = _EN_DOI_RE.sub("", text)
    text = _EN_COPYRIGHT_RE.sub("", text)
    text = _EN_CC_LICENSE_RE.sub("", text)
    text = _EN_ALL_RIGHTS_RE.sub("", text)
    return text


# ---------------------------------------------------------------------------
# Pass B — whitespace and quote cleanup
# ---------------------------------------------------------------------------

# Smart-quote → ASCII (TTS engines often mispronounce typographic quotes).
_SMART_QUOTES = {
    "\u2018": "'",  # left single
    "\u2019": "'",  # right single (also apostrophe)
    "\u201c": '"',  # left double
    "\u201d": '"',  # right double
    "\u2013": "-",  # en dash
    "\u2014": " - ",  # em dash → spaced hyphen for sentence flow
    "\u00a0": " ",  # NBSP
}

# 3+ ASCII periods surrounded by whitespace → Unicode ellipsis (cleaner
# break for the chunker; matches the FI Pass J1 convention).
_EN_ELLIPSIS_RE = re.compile(r"\.{3,}")

# TOC dot leader: 4+ dots followed by a page number at end of line.
_EN_TOC_DOT_LEADER_RE = re.compile(r"\s*\.{4,}\s*\d+\s*$", flags=re.MULTILINE)


def _pass_b_whitespace_quotes(text: str) -> str:
    for src, dst in _SMART_QUOTES.items():
        text = text.replace(src, dst)
    text = _EN_TOC_DOT_LEADER_RE.sub("", text)
    text = _EN_ELLIPSIS_RE.sub("\u2026", text)
    return text


# ---------------------------------------------------------------------------
# Pass C — abbreviations
# ---------------------------------------------------------------------------

# Order matters: longer/more-specific patterns first so "U.S.A." doesn't get
# half-matched by "U.S.". The lookup is applied with word-boundary regex.
_EN_ABBREVIATIONS: list[tuple[str, str]] = [
    # Latin
    ("i.e.",  "that is"),
    ("e.g.",  "for example"),
    ("etc.",  "et cetera"),
    ("cf.",   "compare"),
    ("vs.",   "versus"),
    ("vs",    "versus"),
    ("viz.",  "namely"),
    ("a.m.",  "a m"),
    ("p.m.",  "p m"),
    ("A.M.",  "A M"),
    ("P.M.",  "P M"),
    # Honorifics / titles
    ("Mr.",   "Mister"),
    ("Mrs.",  "Misses"),
    ("Ms.",   "Miss"),
    ("Dr.",   "Doctor"),
    ("Prof.", "Professor"),
    ("Rev.",  "Reverend"),
    ("Hon.",  "Honorable"),
    ("Sr.",   "Senior"),
    ("Jr.",   "Junior"),
    ("Mt.",   "Mount"),
    ("Ft.",   "Fort"),
    # Geographic / state-style
    ("U.S.A.", "U S A"),
    ("U.S.",   "United States"),
    ("U.K.",   "United Kingdom"),
    ("U.N.",   "United Nations"),
    # Numbering / reference
    ("No.",   "Number"),
    ("Vol.",  "Volume"),
    ("Ch.",   "Chapter"),
    ("Fig.",  "Figure"),
    ("Eq.",   "Equation"),
    ("pp.",   "pages"),
    ("p.",    "page"),
]

# St. is context-dependent: "St. Peter" → Saint, "Main St." → Street.
# Heuristic: "St." followed by a capitalized name → Saint; otherwise Street.
_EN_ST_SAINT_RE = re.compile(r"\bSt\.\s+([A-Z][a-zA-Z]+)")
_EN_ST_STREET_RE = re.compile(r"\bSt\.(?!\s+[A-Z])")


def _pass_c_abbreviations(text: str) -> str:
    # Saint/Street disambiguation first so it doesn't fight later passes.
    text = _EN_ST_SAINT_RE.sub(r"Saint \1", text)
    text = _EN_ST_STREET_RE.sub("Street", text)

    for abbr, expansion in _EN_ABBREVIATIONS:
        # Build a regex that handles the literal abbreviation. We escape it
        # and require either word-boundary or the trailing period itself
        # to terminate the match.
        if abbr.endswith("."):
            pattern = re.escape(abbr)
        else:
            pattern = r"\b" + re.escape(abbr) + r"\b"
        text = re.sub(pattern, expansion, text)
    return text


# ---------------------------------------------------------------------------
# Pass D — Roman numerals in context
# ---------------------------------------------------------------------------

# Roman → int, returns None on invalid input.
_ROMAN_VALUES = {
    "I": 1, "V": 5, "X": 10, "L": 50,
    "C": 100, "D": 500, "M": 1000,
}


def _roman_to_int(s: str) -> int | None:
    """Convert a Roman numeral string to int. Returns None if invalid."""
    if not s or not all(c in _ROMAN_VALUES for c in s.upper()):
        return None
    s = s.upper()
    total = 0
    prev = 0
    for ch in reversed(s):
        v = _ROMAN_VALUES[ch]
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    # Round-trip validate: re-encode and compare. Catches things like "IIII"
    # or "VV" that pass the lenient sum but aren't legal Roman.
    return total if _int_to_roman(total) == s else None


def _int_to_roman(n: int) -> str:
    if n <= 0 or n > 3999:
        return ""
    pairs = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"),  (90, "XC"),  (50, "L"),  (40, "XL"),
        (10, "X"),   (9, "IX"),   (5, "V"),   (4, "IV"), (1, "I"),
    ]
    out = []
    for val, sym in pairs:
        while n >= val:
            out.append(sym)
            n -= val
    return "".join(out)


# Words that legitimize a following Roman numeral. Without this guard,
# "I" (pronoun), "MIX" (a word), "DID" (a word) all get false-expanded.
# Two flavors:
#   - Cardinal context: "Chapter IV" → "Chapter four"
#   - Regnal context:   "Louis XIV"  → "Louis the fourteenth"
_EN_ROMAN_CARDINAL_CONTEXTS = {
    "Chapter", "Book", "Part", "Volume", "Section", "Act", "Scene",
    "Article", "Appendix", "Figure", "Table", "Phase", "Stage",
    "Episode", "Series",
}
_EN_ROMAN_REGNAL_CONTEXTS = {
    # Monarchs / popes — non-exhaustive but covers the common cases.
    "Pope", "King", "Queen", "Emperor", "Empress", "Tsar", "Sultan",
    "Louis", "Henry", "Edward", "George", "William", "Richard", "Charles",
    "James", "Elizabeth", "Mary", "Victoria", "Albert", "Frederick",
    "Philip", "Napoleon", "Nicholas", "Alexander", "Peter", "Paul",
    "John", "Pius", "Benedict", "Leo", "Gregory", "Innocent", "Clement",
    "Urban", "Boniface",
}

# Roman-numeral token, uppercase only. Single-char tokens (V, X, L, etc.)
# are allowed because they're legitimate in contexts like "Volume V".
# Bare "I" without a context word is filtered by the substitution
# function's context check; with a context word ("Chapter I", "Henry I")
# expansion is the right call.
_EN_ROMAN_TOKEN_RE = re.compile(r"\b([IVXLCDM]+)\b")


def _ordinal_word(n: int) -> str:
    """Return 'first', 'second', ... for n ≥ 1, falling back to num2words."""
    try:
        from num2words import num2words  # type: ignore
        return num2words(n, lang="en", to="ordinal")
    except ImportError:
        # Tiny fallback for the most common cases — survives a missing dep.
        small = {
            1: "first", 2: "second", 3: "third", 4: "fourth",
            5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth",
            9: "ninth", 10: "tenth",
        }
        return small.get(n, str(n))


def _cardinal_word(n: int) -> str:
    try:
        from num2words import num2words  # type: ignore
        return num2words(n, lang="en")
    except ImportError:
        return str(n)


def _pass_d_roman_in_context(text: str) -> str:
    """Expand Roman numerals only when preceded by a context word."""
    def repl(m: re.Match[str]) -> str:
        roman = m.group(1)
        # Look at the preceding word.
        start = m.start()
        # Walk back over whitespace + capture the prior word.
        before = text[:start].rstrip()
        prev_word_match = re.search(r"([A-Za-z]+)\s*$", before)
        if not prev_word_match:
            return roman  # no context — leave it
        prev_word = prev_word_match.group(1)

        n = _roman_to_int(roman)
        if n is None:
            return roman

        if prev_word in _EN_ROMAN_REGNAL_CONTEXTS:
            return f"the {_ordinal_word(n)}"
        if prev_word in _EN_ROMAN_CARDINAL_CONTEXTS:
            return _cardinal_word(n)
        return roman  # not a recognized context — leave it

    return _EN_ROMAN_TOKEN_RE.sub(repl, text)


# ---------------------------------------------------------------------------
# Pass E — ordinal digit forms
# ---------------------------------------------------------------------------

_EN_ORDINAL_RE = re.compile(r"\b(\d+)(st|nd|rd|th)\b", flags=re.IGNORECASE)


def _pass_e_ordinal_digits(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return _ordinal_word(n)
    return _EN_ORDINAL_RE.sub(repl, text)


# ---------------------------------------------------------------------------
# Pass F — years
# ---------------------------------------------------------------------------

# A 4-digit token in the year range, surrounded by token boundaries.
# We only fire if the surrounding context strongly suggests "year":
#   - sentence-initial / preceded by a year preposition
#   - followed by punctuation, end-of-line, or a year-context word
# Ambiguity cases ("1917 pages") are left for Pass G to handle as cardinal.
_EN_YEAR_RANGE = re.compile(
    r"\b(1\d{3}|20\d{2})\s*[\u2013\-]\s*(1\d{3}|20\d{2})\b"
)
_EN_DECADE_S = re.compile(r"\b(1\d{3}|20\d{2})s\b")
_EN_DECADE_APOS = re.compile(r"'(\d{2})s\b")
# Year preceded by a preposition or sentence start.
_EN_YEAR_PREP_RE = re.compile(
    r"\b(in|by|of|since|until|from|to|around|circa|ca\.?|c\.|"
    r"during|after|before|between)\s+(1\d{3}|20\d{2})\b",
    flags=re.IGNORECASE,
)


def _year_to_words(n: int) -> str:
    try:
        from num2words import num2words  # type: ignore
        return num2words(n, lang="en", to="year")
    except ImportError:
        return _cardinal_word(n)


def _pass_f_years(text: str) -> str:
    # Year ranges first so neither endpoint gets eaten by the singleton rule.
    text = _EN_YEAR_RANGE.sub(
        lambda m: f"{_year_to_words(int(m.group(1)))} to "
                  f"{_year_to_words(int(m.group(2)))}",
        text,
    )
    # Decades: 1920s → "nineteen twenties"
    text = _EN_DECADE_S.sub(
        lambda m: _decade_to_words(int(m.group(1))),
        text,
    )
    # Apostrophe decades: '20s → "twenties"
    text = _EN_DECADE_APOS.sub(
        lambda m: _short_decade_to_words(int(m.group(1))),
        text,
    )
    # Singleton year after a preposition.
    text = _EN_YEAR_PREP_RE.sub(
        lambda m: f"{m.group(1)} {_year_to_words(int(m.group(2)))}",
        text,
    )
    return text


def _decade_to_words(year: int) -> str:
    """1920 → 'nineteen twenties', 2000 → 'two thousands'."""
    century = year // 100
    decade = year % 100
    if decade == 0:
        return f"{_cardinal_word(century * 100)}s"
    base = _year_to_words(year)  # 'nineteen twenty'
    # Replace the last token with its plural decade form when possible.
    parts = base.split()
    if not parts:
        return base + "s"
    last = parts[-1]
    plural = _PLURAL_DECADES.get(last)
    if plural is not None:
        parts[-1] = plural
        return " ".join(parts)
    return base + "s"


def _short_decade_to_words(decade: int) -> str:
    """20 → 'twenties', 60 → 'sixties'."""
    if decade % 10 != 0 or decade > 90:
        return f"'{decade:02d}s"
    word = _cardinal_word(decade)
    return _PLURAL_DECADES.get(word, word + "s")


_PLURAL_DECADES = {
    "twenty": "twenties",
    "thirty": "thirties",
    "forty": "forties",
    "fifty": "fifties",
    "sixty": "sixties",
    "seventy": "seventies",
    "eighty": "eighties",
    "ninety": "nineties",
}


# ---------------------------------------------------------------------------
# Pass G — cardinal integers
# ---------------------------------------------------------------------------

# Match standalone integers (with optional thousands separator and sign).
# Keep it simple: integer surrounded by word boundaries, no decimal point.
_EN_CARDINAL_RE = re.compile(r"(?<![\d.])-?\d{1,3}(?:,\d{3})+(?![\d.])"
                              r"|(?<![\d.])-?\d+(?![\d.])")


def _pass_g_cardinal(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        token = m.group(0)
        # Strip thousands separators.
        clean = token.replace(",", "")
        try:
            n = int(clean)
        except ValueError:
            return token
        word = _cardinal_word(abs(n))
        if n < 0:
            return f"minus {word}"
        return word
    return _EN_CARDINAL_RE.sub(repl, text)


# ---------------------------------------------------------------------------
# Pass H — decimals
# ---------------------------------------------------------------------------

_EN_DECIMAL_RE = re.compile(r"(?<!\d)(-?)(\d+)\.(\d+)(?!\d)")


def _pass_h_decimals(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        sign, whole, frac = m.group(1), m.group(2), m.group(3)
        whole_word = _cardinal_word(int(whole))
        frac_words = " ".join(_cardinal_word(int(d)) for d in frac)
        prefix = "minus " if sign == "-" else ""
        return f"{prefix}{whole_word} point {frac_words}"
    return _EN_DECIMAL_RE.sub(repl, text)


# ---------------------------------------------------------------------------
# Pass I — fractions
# ---------------------------------------------------------------------------

_FRACTION_DENOMINATORS = {
    2: "half", 3: "third", 4: "quarter", 5: "fifth", 6: "sixth",
    7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
    11: "eleventh", 12: "twelfth", 16: "sixteenth", 32: "thirty-second",
    64: "sixty-fourth", 100: "hundredth",
}

_EN_FRACTION_RE = re.compile(r"\b(\d+)/(\d+)\b")


def _pass_i_fractions(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        num = int(m.group(1))
        den = int(m.group(2))
        if den == 0:
            return m.group(0)
        num_word = _cardinal_word(num)
        den_word = _FRACTION_DENOMINATORS.get(den)
        if den_word is None:
            den_word = _ordinal_word(den)
        if num != 1:
            den_word = den_word + "s"
        return f"{num_word} {den_word}"
    return _EN_FRACTION_RE.sub(repl, text)


# ---------------------------------------------------------------------------
# Pass J — sentence-terminal period normalization
# ---------------------------------------------------------------------------

# Collapse "word . " → "word. " and " ." → "."
_EN_LOOSE_PERIOD_RE = re.compile(r"\s+\.")


def _pass_j_periods(text: str) -> str:
    return _EN_LOOSE_PERIOD_RE.sub(".", text)


# ---------------------------------------------------------------------------
# Pass K — final whitespace collapse
# ---------------------------------------------------------------------------

_EN_MULTI_WS_RE = re.compile(r"[ \t]+")
_EN_MULTI_NL_RE = re.compile(r"\n{3,}")


def _pass_k_whitespace(text: str) -> str:
    text = _EN_MULTI_WS_RE.sub(" ", text)
    text = _EN_MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def normalize_english_text(
    text: str,
    *,
    _lang: str | None = None,
) -> str:
    """Normalize English text for TTS audiobook synthesis.

    Args:
        text: Raw English text.
        _lang: Optional language guard used by the dispatcher. When the
            dispatcher routes a non-English request to this module by
            mistake, a ``LanguageMismatchError`` is raised instead of
            silently rewriting Finnish text with English rules.

    Returns:
        Normalized text ready for the TTS engine.
    """
    if _lang is not None and _lang != _MY_LANG:
        from src.tts_normalizer import LanguageMismatchError
        raise LanguageMismatchError(
            f"normalize_english_text called with _lang={_lang!r}; "
            f"this module only handles {_MY_LANG!r}. "
            f"Use src.tts_normalizer.normalize_text instead."
        )
    if not text:
        return text

    try:
        from num2words import num2words  # noqa: F401
    except ImportError:
        # Without num2words we can't expand numbers — return unchanged
        # rather than emit half-normalised junk.
        return text

    text = _pass_a_metadata_strip(text)
    text = _pass_b_whitespace_quotes(text)
    text = _pass_c_abbreviations(text)
    text = _pass_d_roman_in_context(text)
    text = _pass_e_ordinal_digits(text)
    text = _pass_f_years(text)
    # Fractions and decimals must run BEFORE the cardinal sweep, otherwise
    # G converts the digits in "1/2" or "3.14" individually and the
    # fraction/decimal regexes no longer match.
    text = _pass_i_fractions(text)
    text = _pass_h_decimals(text)
    text = _pass_g_cardinal(text)
    text = _pass_j_periods(text)
    text = _pass_k_whitespace(text)
    return text
