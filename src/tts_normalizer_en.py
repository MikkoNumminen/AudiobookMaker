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

import functools
import re

# NOTE: _pass_o_dates is imported lazily inside normalize_english_text()
# below to avoid a circular import — _en_pass_o_dates itself imports
# _cardinal_word / _ordinal_word / _year_to_words from this module.

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
# half-matched by "U.S.". The table itself lives in data/en_abbreviations.yaml
# so non-developers can curate the lexicon without editing Python. The lookup
# is applied with word-boundary regex.
@functools.lru_cache(maxsize=1)
def _load_abbreviations() -> list[tuple[str, str]]:
    from src._yaml_data import load_yaml
    raw = load_yaml("en_abbreviations")
    if not raw:
        return []
    return [(str(pair[0]), str(pair[1])) for pair in raw]

# St. is context-dependent: "St. Peter" → Saint, "Main St." → Street.
# Heuristic: "St." followed by a capitalized name → Saint; otherwise Street.
_EN_ST_SAINT_RE = re.compile(r"\bSt\.\s+([A-Z][a-zA-Z]+)")
_EN_ST_STREET_RE = re.compile(r"\bSt\.(?!\s+[A-Z])")


@functools.lru_cache(maxsize=None)
def _get_abbrev_re(abbr: str) -> re.Pattern[str]:
    # Build a regex that handles the literal abbreviation. We escape it
    # and require either word-boundary or the trailing period itself
    # to terminate the match.
    if abbr.endswith("."):
        pattern = re.escape(abbr)
    else:
        pattern = r"\b" + re.escape(abbr) + r"\b"
    return re.compile(pattern)


def _pass_c_abbreviations(text: str) -> str:
    # Saint/Street disambiguation first so it doesn't fight later passes.
    text = _EN_ST_SAINT_RE.sub(r"Saint \1", text)
    text = _EN_ST_STREET_RE.sub("Street", text)

    for abbr, expansion in _load_abbreviations():
        text = _get_abbrev_re(abbr).sub(expansion, text)
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
# The actual vocab lives in data/en_roman_contexts.yaml.
@functools.lru_cache(maxsize=1)
def _load_roman_contexts() -> tuple[frozenset[str], frozenset[str]]:
    from src._yaml_data import load_yaml
    raw = load_yaml("en_roman_contexts") or {}
    cardinal = frozenset(str(w) for w in (raw.get("cardinal") or []))
    regnal = frozenset(str(w) for w in (raw.get("regnal") or []))
    return cardinal, regnal

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

        cardinal_ctx, regnal_ctx = _load_roman_contexts()
        if prev_word in regnal_ctx:
            return f"the {_ordinal_word(n)}"
        if prev_word in cardinal_ctx:
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
# Pass L — currency
# ---------------------------------------------------------------------------

# Currency symbol → spoken word. Singular/plural decided per-amount.
_EN_CURRENCY_SYMBOLS = {
    "$":  ("dollar", "dollars"),
    "£":  ("pound", "pounds"),
    "€":  ("euro", "euros"),
    "¥":  ("yen", "yen"),
    "₹":  ("rupee", "rupees"),
    "₽":  ("rouble", "roubles"),
}

# ISO codes / suffixes after the amount: "5 USD", "10 GBP".
_EN_CURRENCY_CODES = {
    "USD": ("dollar", "dollars"),
    "GBP": ("pound", "pounds"),
    "EUR": ("euro", "euros"),
    "JPY": ("yen", "yen"),
    "INR": ("rupee", "rupees"),
}

# Magnitude suffix attached to a currency amount: "$1.5M", "€2K".
_EN_AMOUNT_MAGNITUDES = {
    "K": "thousand", "M": "million", "B": "billion", "T": "trillion",
}

# Number with optional thousands separator and optional decimal part.
_AMOUNT_FRAGMENT = r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?"

# "$1,234.56" / "$5" / "$1.5M"
_EN_CURRENCY_SYMBOL_RE = re.compile(
    r"([$£€¥₹₽])\s*(" + _AMOUNT_FRAGMENT + r")(K|M|B|T)?\b"
)
# "5 USD" / "10.50 GBP"
_EN_CURRENCY_CODE_RE = re.compile(
    r"\b(" + _AMOUNT_FRAGMENT + r")(K|M|B|T)?\s+(USD|GBP|EUR|JPY|INR)\b"
)


def _spoken_amount(amount_str: str) -> tuple[str, float]:
    """Return ('one thousand two hundred', 1234.0) for '1,234'."""
    clean = amount_str.replace(",", "")
    try:
        value = float(clean)
    except ValueError:
        return amount_str, 0.0
    if value == int(value):
        return _cardinal_word(int(value)), value
    # Decimal — split into whole + fractional digits.
    whole, _, frac = clean.partition(".")
    whole_w = _cardinal_word(int(whole))
    frac_w = " ".join(_cardinal_word(int(d)) for d in frac)
    return f"{whole_w} point {frac_w}", value


def _verbalize_currency(amount_str: str, magnitude: str | None,
                        unit_words: tuple[str, str]) -> str:
    spoken, value = _spoken_amount(amount_str)
    singular, plural = unit_words
    # Magnitude suffix turns it into a count of millions / etc.; the
    # currency unit becomes plural when the multiplier ≠ 1.
    if magnitude:
        mag_word = _EN_AMOUNT_MAGNITUDES[magnitude]
        unit = plural  # "1.5 million dollars"
        return f"{spoken} {mag_word} {unit}"
    # Cents-style decimal: "$5.99" → "five dollars and ninety nine cents".
    if "." in amount_str and unit_words in (
            ("dollar", "dollars"), ("pound", "pounds"),
            ("euro", "euros")):
        whole_str, _, frac_str = amount_str.replace(",", "").partition(".")
        whole = int(whole_str)
        # Pad/truncate to 2 fractional digits for cents.
        cents_str = (frac_str + "00")[:2]
        cents = int(cents_str)
        unit = plural if whole != 1 else singular
        sub_unit_singular = {
            ("dollar", "dollars"): "cent",
            ("pound", "pounds"):   "penny",
            ("euro", "euros"):     "cent",
        }[unit_words]
        sub_unit_plural = {
            ("dollar", "dollars"): "cents",
            ("pound", "pounds"):   "pence",
            ("euro", "euros"):     "cents",
        }[unit_words]
        whole_w = _cardinal_word(whole)
        if cents == 0:
            return f"{whole_w} {unit}"
        cents_w = _cardinal_word(cents)
        sub_unit = sub_unit_plural if cents != 1 else sub_unit_singular
        return f"{whole_w} {unit} and {cents_w} {sub_unit}"
    unit = plural if value != 1 else singular
    return f"{spoken} {unit}"


def _pass_l_currency(text: str) -> str:
    def repl_symbol(m: re.Match[str]) -> str:
        symbol, amount, mag = m.group(1), m.group(2), m.group(3)
        unit = _EN_CURRENCY_SYMBOLS[symbol]
        return _verbalize_currency(amount, mag, unit)

    def repl_code(m: re.Match[str]) -> str:
        amount, mag, code = m.group(1), m.group(2), m.group(3)
        unit = _EN_CURRENCY_CODES[code]
        return _verbalize_currency(amount, mag, unit)

    text = _EN_CURRENCY_SYMBOL_RE.sub(repl_symbol, text)
    text = _EN_CURRENCY_CODE_RE.sub(repl_code, text)
    return text


# ---------------------------------------------------------------------------
# Pass M — units / measurements
# ---------------------------------------------------------------------------

# Unit symbol → (singular, plural). Coverage targets common audiobook
# encounters: distance, mass, volume, temperature, time, data, speed.
# The table lives in data/en_units.yaml; temperature units are handled
# separately in Python because they want the "degree" word.
@functools.lru_cache(maxsize=1)
def _load_units() -> dict[str, tuple[str, str]]:
    from src._yaml_data import load_yaml
    raw = load_yaml("en_units") or {}
    return {
        str(sym): (str(pair[0]), str(pair[1]))
        for sym, pair in raw.items()
    }


@functools.lru_cache(maxsize=1)
def _get_unit_re() -> re.Pattern[str]:
    # Build a single pattern. Order longer-first so "kib" wins over "k".
    units = _load_units()
    pattern = "|".join(
        re.escape(u) for u in sorted(units, key=len, reverse=True)
    )
    if not pattern:
        # No units loaded — use an impossible pattern so the regex never
        # fires (the unit pass becomes a no-op).
        pattern = r"(?!x)x"
    return re.compile(
        r"\b(" + _AMOUNT_FRAGMENT + r")\s*(" + pattern + r")\b",
        flags=re.IGNORECASE,
    )
# Temperature needs separate treatment: "32 °F", "100°C".
_EN_TEMP_RE = re.compile(
    r"\b(" + _AMOUNT_FRAGMENT + r")\s*°\s*([CFK])\b"
)
_TEMP_UNIT = {
    "C": ("degree Celsius", "degrees Celsius"),
    "F": ("degree Fahrenheit", "degrees Fahrenheit"),
    "K": ("kelvin", "kelvin"),
}


def _pass_m_units(text: str) -> str:
    def repl_temp(m: re.Match[str]) -> str:
        amount, scale = m.group(1), m.group(2)
        spoken, value = _spoken_amount(amount)
        unit = _TEMP_UNIT[scale]
        word = unit[1] if value != 1 else unit[0]
        return f"{spoken} {word}"

    def repl_unit(m: re.Match[str]) -> str:
        amount, unit = m.group(1), m.group(2).lower()
        units = _load_units().get(unit)
        if units is None:
            return m.group(0)
        spoken, value = _spoken_amount(amount)
        word = units[1] if value != 1 else units[0]
        return f"{spoken} {word}"

    text = _EN_TEMP_RE.sub(repl_temp, text)
    text = _get_unit_re().sub(repl_unit, text)
    return text


# ---------------------------------------------------------------------------
# Pass N — time of day
# ---------------------------------------------------------------------------

# Time-of-day pattern: HH:MM with hour 0-23, minute 0-59. Word boundaries
# keep it from eating chapter:verse markers written without a colon, and
# the explicit colon means ratios like "3 to 14" don't trigger here.
_EN_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")

# Words for the "teens" part of a minute: 01-09 read as "oh one" ... "oh nine"
# to keep the audiobook cadence natural ("nine oh five" beats "nine five").
_MINUTE_TENS_WORD = {
    2: "twenty", 3: "thirty", 4: "forty", 5: "fifty",
}


def _minute_to_words(minute: int) -> str:
    """Spoken English for the minute half of a time.

    0       → "o'clock"  (caller handles the whole-hour form)
    1–9     → "oh one" ... "oh nine"
    10–59   → "ten", "eleven", ..., "twenty one", ..., "fifty nine"
    """
    if minute == 0:
        return "o'clock"
    if 1 <= minute <= 9:
        return f"oh {_cardinal_word(minute)}"
    # num2words yields "twenty-one" with a hyphen; keep that — it matches
    # the rest of this module (_FRACTION_DENOMINATORS uses "thirty-second"
    # too) and most TTS engines read hyphenated compounds correctly.
    return _cardinal_word(minute)


def _pass_n_time(text: str) -> str:
    """Verbalize HH:MM time-of-day strings.

    Whole hours in the 12-hour range render as "<hour> o'clock" because
    audiobooks almost never want the "twelve hundred hours" military
    phrasing for civilian times — "it was twelve o'clock" reads naturally
    while "twelve hundred hours" drops the listener into a war novel.
    24-hour whole hours (13:00–23:00 and 00:00) keep the military
    "<hour> hundred hours" form since that's the context in which those
    digits appear in prose.
    """
    def repl(m: re.Match[str]) -> str:
        hour = int(m.group(1))
        minute = int(m.group(2))
        # Defensive re-check; the regex already bounds these but be safe.
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return m.group(0)

        # Whole-hour forms
        if minute == 0:
            if 1 <= hour <= 12:
                return f"{_cardinal_word(hour)} o'clock"
            # 0:00 and 13:00–23:00 use the military reading.
            return f"{_cardinal_word(hour)} hundred hours"

        # Minute-bearing forms read naturally in both 12- and 24-hour
        # contexts: "three forty-five", "fifteen thirty", "nine oh five".
        return f"{_cardinal_word(hour)} {_minute_to_words(minute)}"

    return _EN_TIME_RE.sub(repl, text)


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

    Pass ordering invariants:
        The passes below run in a fixed sequence. Each bullet explains WHY
        the earlier pass must finish before the later one — reorder any of
        them and the later pass silently produces wrong output. These are
        load-bearing; do not shuffle them without updating this list.

        - A and B must run before C: Pass A strips ISBN/DOI/license noise
          and Pass B maps smart quotes and ellipses to ASCII. Pass C's
          abbreviation regexes only recognise the ASCII forms, so leaving
          typographic quotes or noise tokens around lets abbreviations
          slip through unexpanded.
        - R (URLs/emails) must run before C: Pass R verbalises ``@``,
          ``.``, and ``/`` inside URL and email spans. If C ran first the
          period inside ``foo.com`` would look exactly like the period in
          ``etc.`` or ``Mr.`` and the abbreviation pass would butcher it.
          Claim URL spans first, expand abbreviations second.
        - C must run before D: Pass D (Roman numerals in context) looks
          at the preceding word to decide cardinal vs. regnal vs. leave
          alone. If C has not yet expanded ``Dr.`` / ``Mr.`` the Roman
          detector sees a garbled context word and misclassifies the
          numeral.
        - D must run before S (acronyms): tokens like ``IV``, ``XII``,
          ``MCM`` are valid Roman numerals AND look like acronyms. Pass D
          gets first crack so ``Chapter IV`` becomes ``Chapter four``
          before the acronym pass would read them letter-by-letter.
        - E (ordinal digits) must run before F and G: Pass E owns forms
          like ``1st``, ``21st``, ``2nd``. If F or G ran first the bare
          ``1``/``21``/``2`` would be expanded as a year or cardinal and
          the trailing ``st``/``nd`` would be left dangling.
        - L, M, N, O must run before F and G: Pass L (currency), M
          (units), N (time-of-day), and O (dates) each grab typed digit
          patterns that live inside a specific context — ``$5``,
          ``5 km``, ``12:30``, ``March 3, 2020``. Running the broad year
          or cardinal sweeps first would consume the digits as generic
          numbers and the typed passes would have nothing to match.
        - P must run before G: Pass P (telephone) owns phone-shaped digit
          groups like ``555-1234``. If G ran first those digits would be
          read as cardinals and the phone pattern would never fire.
        - F must run before G: Pass F (years) recognises four-digit year
          tokens via surrounding prepositions (``in 1917``). Pass G's
          cardinal regex would otherwise read ``1917`` as "one thousand
          nine hundred seventeen" instead of "nineteen seventeen". Years
          first, cardinals second.
        - I and H must run before G: Pass I (fractions) owns ``1/2`` and
          Pass H (decimals) owns ``3.14``. Pass G's cardinal regex would
          otherwise consume the individual digits and the fraction /
          decimal regexes would never see an intact pattern. This is
          called out explicitly in the code comment above the G call.
        - J and K run last: Pass J (sentence-terminal period cleanup) and
          Pass K (whitespace collapse) tidy up the output. They must run
          after every pass that can inject stray whitespace or periods
          (abbreviation expansion, metadata deletion, etc.), otherwise
          the cleanup leaves artefacts behind.

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
    # URLs/emails before C — verbalising `@`, `.`, `/` only inside matched
    # URL spans avoids confusing the abbreviation pass with patterns like
    # `Mr.` or `etc.` that share the period character.
    from src._en_pass_r_urls import _pass_r_urls_emails
    text = _pass_r_urls_emails(text)
    text = _pass_c_abbreviations(text)
    text = _pass_d_roman_in_context(text)
    # Acronyms after D so `IV`, `XII` etc. (handled by Pass D as Roman
    # numerals when in a regnal/cardinal context) aren't pre-empted.
    from src._en_pass_s_acronyms import _pass_s_acronyms
    text = _pass_s_acronyms(text)
    text = _pass_e_ordinal_digits(text)
    # Phase 2 typed-number passes — must run BEFORE the broad year /
    # cardinal / decimal sweeps so currency, units, etc. consume their
    # digits with the correct context.
    text = _pass_l_currency(text)
    text = _pass_m_units(text)
    text = _pass_n_time(text)
    from src._en_pass_o_dates import _pass_o_dates  # lazy: avoid circular import
    text = _pass_o_dates(text)
    # Telephone before G so phone-shaped digit groups don't get cardinal-read.
    from src._en_pass_p_telephone import _pass_p_telephone
    text = _pass_p_telephone(text)
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
