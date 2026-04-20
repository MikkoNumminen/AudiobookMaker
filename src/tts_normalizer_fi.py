"""Finnish text normalizer for TTS.

Extracted from ``src/tts_engine.py`` as part of the engine split. The
normalizer runs a fixed sequence of passes (A, B, C, ..., N) that rewrite
Finnish-specific patterns so any downstream TTS engine reads them
correctly. Entry point: :func:`normalize_finnish_text`.

Pure text in / text out. No audio or synthesis dependencies.
"""

from __future__ import annotations

import functools
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

# Pass O — emoji strip.
#
# Removes Unicode emoji from text before any other pass touches it. TTS
# engines either skip them silently, mispronounce them, or (worst case)
# read out the Unicode codepoint name. We strip them entirely because
# audiobook prose almost never uses emoji intentionally; when it does,
# losing them is preferable to hearing "unicorn face" mid-sentence.
#
# The character ranges below cover the main emoji blocks plus their
# modifiers (skin tones, variation selector-16, ZWJ, regional indicators).
# Sourced from Unicode 15.1 "Emoji" property; conservative — Latin-1
# punctuation and dingbats commonly used in books (©, ®, ™, ★) are NOT
# stripped because they often carry meaning.
_FI_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"   # Misc symbols & pictographs
    "\U0001F600-\U0001F64F"   # Emoticons (smileys)
    "\U0001F680-\U0001F6FF"   # Transport & map symbols
    "\U0001F700-\U0001F77F"   # Alchemical
    "\U0001F780-\U0001F7FF"   # Geometric shapes extended
    "\U0001F800-\U0001F8FF"   # Supplemental arrows-C
    "\U0001F900-\U0001F9FF"   # Supplemental symbols & pictographs
    "\U0001FA00-\U0001FA6F"   # Chess symbols
    "\U0001FA70-\U0001FAFF"   # Symbols & pictographs extended-A
    "\U0001F1E6-\U0001F1FF"   # Regional indicator symbols (flags)
    "\U0001F3FB-\U0001F3FF"   # Skin tone modifiers
    "\U00002702-\U000027B0"   # Dingbats (✂ ✈ ✉ ✏ ✨ ❌ ❤ etc.)
    "\u2600-\u26FF"           # Misc symbols (☀ ☁ ☎ ⚡ ♻ etc.)
    "\uFE0F"                  # Variation selector-16 (emoji presentation)
    "\u200D"                  # Zero-width joiner (ZWJ sequences)
    "]+",
    flags=re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    """Strip emoji and emoji-related codepoints (Pass O).

    Replaces consecutive emoji runs with a single space so adjacent words
    do not get glued together (`hello👍world` → `hello world`). The final
    whitespace cleanup pass collapses any double spaces.
    """
    return _FI_EMOJI_RE.sub(" ", text)


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

# Final-pass whitespace tidy (run once per chunk).
_FI_WHITESPACE_CLEANUP_RE = re.compile(r"[ \t]+")
_FI_SPACE_BEFORE_PUNCT_RE = re.compile(r" +([.,;:!?])")

# Pass K: Finnish abbreviation expansion.
#
# Expands common Finnish abbreviations to their full spoken forms.
# Must run before Pass C (century expressions) so that abbreviation
# periods do not interfere with period-sensitive downstream patterns.
# All expansions are emitted in lowercase regardless of input case.
#
# The `tri` trigger is special — it has no trailing period and only
# expands before a space + capital letter (a person's name).

# Abbreviation table — lexicon lives in data/fi_abbreviations.yaml.
@functools.lru_cache(maxsize=1)
def _fi_abbrev_map() -> dict[str, str]:
    from src._yaml_data import load_yaml
    raw = load_yaml("fi_abbreviations") or {}
    return {str(k): str(v) for k, v in raw.items()}


@functools.lru_cache(maxsize=1)
def _fi_abbrev_re() -> re.Pattern[str]:
    # Build one regex alternation sorted longest-first to prevent partial matches.
    keys = sorted(_fi_abbrev_map(), key=len, reverse=True)
    if not keys:
        return re.compile(r"(?!x)x")
    return re.compile(
        r"\b(" + "|".join(re.escape(k) for k in keys) + r")",
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
    abbrev_map = _fi_abbrev_map()

    def _abbrev_sub(m: re.Match) -> str:
        key = m.group(1).lower()
        # eKr. and jKr. have mixed case keys — look up both variants
        expansion = abbrev_map.get(key)
        if expansion is None:
            # Try original capitalisation (for eKr. / jKr.)
            expansion = abbrev_map.get(m.group(1))
        return expansion if expansion is not None else m.group(1)

    text = _fi_abbrev_re().sub(_abbrev_sub, text)
    text = _FI_TRI_RE.sub("tohtori", text)
    return text


# Pass T: Finnish date and clock-time expansion.
#
# Catches two common formats that downstream digit passes (D/F/G) would
# otherwise mangle:
#
#   - Numeric date `D.M.YYYY` or `DD.MM.YYYY` (Finnish convention) →
#     "{day-ordinal} {month-name-partitive} {year-cardinal}".
#     Example: "14.4.2026" → "neljästoista huhtikuuta kaksituhatta
#     kaksikymmentäkuusi". Requires a 4-digit year so we don't eat
#     decimal numbers like "3.14" or version strings like "1.0.2".
#
#   - Clock time `klo HH:MM` or `kello HH:MM` →
#     "kello {hour-cardinal} {minute-cardinal}".
#     Example: "klo 20:30" → "kello kaksikymmentä kolmekymmentä".
#     Standalone `HH:MM` without a `klo`/`kello` prefix is NOT touched,
#     to avoid mangling sports scores, ratios, or chapter numbering.
#
# Must run BEFORE Pass C (centuries: `1500-luvulla` doesn't collide here
# but date passes care about period-separated digits), Pass D (numeric
# ranges: would split the day-month-year on the dots... actually D only
# touches dashes, so no conflict — but ordering kept conservative), Pass
# F (decimals: `14.4.2026` looks like decimal `14.4` to F), and Pass G
# (cardinal expansion: would expand each digit run independently).
# Order relative to Pass K is irrelevant — Pass K targets word
# abbreviations like `klo.`, while this pass matches `klo` with no
# trailing period (the in-time form). Both `klo` and `kello` are
# accepted as the prefix here for robustness.

# Finnish month names — partitive form ("of June", "of April"). The
# partitive is the form used after an ordinal day in spoken date
# readouts ("neljäs huhtikuuta", "kolmas tammikuuta").
_FI_MONTH_PARTITIVE: tuple[str, ...] = (
    "tammikuuta",   # 1
    "helmikuuta",   # 2
    "maaliskuuta",  # 3
    "huhtikuuta",   # 4
    "toukokuuta",   # 5
    "kesäkuuta",    # 6
    "heinäkuuta",   # 7
    "elokuuta",     # 8
    "syyskuuta",    # 9
    "lokakuuta",    # 10
    "marraskuuta",  # 11
    "joulukuuta",   # 12
)

# Date: `D.M.YYYY` or `DD.MM.YYYY`. 4-digit year is mandatory so the
# regex doesn't accidentally swallow decimal numbers or version strings.
_FI_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")

# Clock time: `klo` or `kello` followed by HH:MM. The prefix is mandatory
# to avoid eating ratios / sports scores / chapter ranges. The hour and
# minute are validated in the substitution function (0–23, 0–59).
_FI_TIME_RE = re.compile(r"\b(?:klo|kello)\s+(\d{1,2}):(\d{2})\b", re.IGNORECASE)


def _expand_dates_and_times(text: str) -> str:
    """Expand Finnish dates and clock times to spoken form (Pass T).

    Date format: ``D.M.YYYY`` → ``{day-ordinal} {month-partitive} {year}``.
    Time format: ``klo HH:MM`` / ``kello HH:MM`` →
    ``kello {hour-cardinal} {minute-cardinal}``.

    Invalid dates (day > 31, month > 12) and invalid times (hour > 23,
    minute > 59) are left unchanged so that pathological inputs fall
    through to the later passes unmolested.
    """
    try:
        from num2words import num2words  # type: ignore
    except ImportError:
        return text

    def _date_sub(m: re.Match[str]) -> str:
        day = int(m.group(1))
        month = int(m.group(2))
        year = int(m.group(3))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return m.group(0)
        try:
            day_word = num2words(day, lang="fi", to="ordinal")
            year_word = num2words(year, lang="fi")
        except (NotImplementedError, OverflowError, ValueError, TypeError):
            return m.group(0)
        month_word = _FI_MONTH_PARTITIVE[month - 1]
        return f"{day_word} {month_word} {year_word}"

    def _time_sub(m: re.Match[str]) -> str:
        hour = int(m.group(1))
        minute = int(m.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return m.group(0)
        try:
            hour_word = num2words(hour, lang="fi")
            minute_word = num2words(minute, lang="fi")
        except (NotImplementedError, OverflowError, ValueError, TypeError):
            return m.group(0)
        return f"kello {hour_word} {minute_word}"

    text = _FI_DATE_RE.sub(_date_sub, text)
    text = _FI_TIME_RE.sub(_time_sub, text)
    return text


# Pass N: Finnish acronym expansion (known whitelist + letter-by-letter fallback).
#
# Step 1 — expand a fixed set of acronyms to their Finnish spoken forms
# (``_expand_acronyms``). Matching is exact-case and word-boundary anchored so
# that:
#   - lowercase `eu` (negative prefix) is NOT expanded
#   - inflected forms like `NATOn` or `EU:n` may or may not match depending
#     on whether the boundary falls (see _expand_acronyms docstring)
#
# Step 2 — letter-by-letter fallback (``_expand_acronym_fallback``) for
# 2–5 letter ALL-CAPS tokens that survived step 1. Spells them with spaces
# between letters so Chatterbox reads "XKJ" as three separate letters
# instead of trying to pronounce it as a garbled word. Accented Finnish
# uppercase (Ä, Ö, Å) stays out of ``[A-Z]`` so real Finnish all-caps
# words like "SÄÄ" and "TYÖ" are naturally excluded. Headings (a run of
# 3+ all-caps tokens) are left alone to keep chapter titles readable.
#
# Run AFTER Pass K (abbreviations) and BEFORE Pass M (units).

# Acronym lookup — lexicon lives in data/fi_acronyms.yaml.
@functools.lru_cache(maxsize=1)
def _fi_acronym_lookup() -> dict[str, str]:
    from src._yaml_data import load_yaml
    raw = load_yaml("fi_acronyms") or {}
    return {str(k): str(v) for k, v in raw.items()}


@functools.lru_cache(maxsize=1)
def _fi_acronym_re() -> re.Pattern[str]:
    # Build a regex alternation sorted longest-first so `ABGB` matches
    # before `BGB`, etc. Case-sensitive: `EU` ≠ `eu` (negative prefix).
    keys = sorted(_fi_acronym_lookup(), key=len, reverse=True)
    if not keys:
        return re.compile(r"(?!x)x")
    return re.compile(
        r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b"
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
    lookup = _fi_acronym_lookup()

    def _sub(m: re.Match) -> str:
        return lookup[m.group(1)]
    return _fi_acronym_re().sub(_sub, text)


# --- Pass N step 2: letter-by-letter fallback for unknown all-caps tokens ---
#
# 2-4 uppercase A-Z letters, bounded by word boundaries. The upper bound is
# 4 (not 5) so real Finnish words commonly written in all caps in headings
# or emphasis (e.g. "RAJAT", "HÄNEN") are not spelled out. Accented letters
# (Ä, Ö, Å) are also excluded — all-caps Finnish words that contain them
# (SÄÄ, TYÖ, PÄÄ) are real words that the TTS model can already read.
_FI_ACRONYM_FALLBACK_RE = re.compile(r"\b[A-Z]{2,4}\b")

# Common short Finnish words that sometimes appear in all-caps inside
# prose (emphasis, OCR artifacts, contrived test fixtures). These must
# NOT be spelled letter-by-letter. This list is intentionally small —
# grow it when a real audiobook flags a regression.
_FI_NONACRONYM_WORDS: frozenset[str] = frozenset({
    "JA", "JO", "ON", "OS", "SE", "EI", "EN",
    "JOS", "KUN", "NYT", "MUT", "TAI", "MIT", "NIN",
})

# Used for the heading-run heuristic: a whitespace-separated token that is
# entirely uppercase Latin letters (any length >= 2). Accented chars are
# allowed here because a heading like "VAARALLISIA SÄÄTILOJA" still reads
# as a heading even though it contains Ä.
_FI_ALLCAPS_NEIGHBOR_RE = re.compile(r"^[A-ZÅÄÖ]{2,}$")


def _fi_is_allcaps_neighbor(tok: str) -> bool:
    """Return True if ``tok`` looks like another ALL-CAPS word."""
    if not tok:
        return False
    # Strip trailing punctuation so "LUKU," still counts as a heading token.
    stripped = tok.strip(".,:;!?\"'()[]{}")
    return bool(_FI_ALLCAPS_NEIGHBOR_RE.match(stripped))


def _fi_heading_run_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans for runs of 3+ consecutive ALL-CAPS
    (length >= 2) whitespace-separated tokens. Tokens inside these spans
    are treated as headings and left alone by the fallback."""
    spans: list[tuple[int, int]] = []
    tokens: list[tuple[int, int, str]] = []
    for m in re.finditer(r"\S+", text):
        tokens.append((m.start(), m.end(), m.group(0)))

    i = 0
    n = len(tokens)
    while i < n:
        if _fi_is_allcaps_neighbor(tokens[i][2]):
            j = i
            while j < n and _fi_is_allcaps_neighbor(tokens[j][2]):
                j += 1
            if j - i >= 3:
                spans.append((tokens[i][0], tokens[j - 1][1]))
            i = j
        else:
            i += 1
    return spans


def _fi_in_heading_run(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    for s, e in spans:
        if s <= start and end <= e:
            return True
    return False


def _expand_acronym_fallback(text: str) -> str:
    """Spell unknown 2-5 letter ALL-CAPS tokens letter-by-letter.

    Runs after :func:`_expand_acronyms`, so any token listed in
    ``data/fi_acronyms.yaml`` has already been replaced with its Finnish
    spoken form and won't reach this pass. Idempotent: once a token
    becomes ``X K J``, the individual letters are length 1 and no longer
    match the 2-5 letter pattern.

    Caveats:
    - Common short Finnish words (``JA``, ``ON``, ``NYT`` etc.) are
      protected by ``_FI_NONACRONYM_WORDS``; grow that set if a new
      regression surfaces.
    - A single all-caps token inside a 3+ all-caps heading run is left
      alone via :func:`_fi_heading_run_spans`.
    - Tokens of 5+ characters (often Finnish words written as chapter
      headings, e.g. ``RAJAT``) never match the regex and are always
      left alone.
    """
    if not text:
        return text

    heading_spans = _fi_heading_run_spans(text)

    def _sub(m: re.Match[str]) -> str:
        tok = m.group(0)
        if tok in _FI_NONACRONYM_WORDS:
            return tok
        if _fi_in_heading_run(heading_spans, m.start(), m.end()):
            return tok
        return " ".join(tok)

    return _FI_ACRONYM_FALLBACK_RE.sub(_sub, text)


# Pass M: measurement unit / currency symbol expansion.
#
# Replaces numeric-prefixed unit symbols with their Finnish partitive forms
# so Pass G's governor detection sees `5 prosenttia` and picks nominative
# from `_FI_GOVERNOR_AFTER["prosenttia"]`.  Must run before Pass C/D/F/G.
#
# Units are ordered longest-first in the alternation to avoid partial matches
# (e.g. `°C` before bare `C`, `km` before bare `m`).

# Unit/currency table — lexicon lives in data/fi_units.yaml. The order
# of entries in the YAML is preserved by safe_load because it is written
# as a YAML sequence; we rely on that order for multi-char-first regex
# alternation (e.g. `km` before `m`, `°C` before `C`).
@functools.lru_cache(maxsize=1)
def _fi_unit_map() -> list[tuple[str, str]]:
    from src._yaml_data import load_yaml
    raw = load_yaml("fi_units") or []
    return [(str(pair[0]), str(pair[1])) for pair in raw]


@functools.lru_cache(maxsize=1)
def _fi_unit_re() -> re.Pattern[str]:
    # Build a single regex: (-?\d+(?:[.,]\d+)?)\s*(<unit>) followed by a
    # negative lookahead for word characters so word-character units like
    # `km` are not greedily matched mid-word, while symbol units like
    # `%` and `€` (which are non-word chars and don't support `\b`) still
    # match correctly. Units follow the YAML order (longest-first).
    units = _fi_unit_map()
    if not units:
        return re.compile(r"(?!x)x")
    return re.compile(
        r"(-?\d+(?:[.,]\d+)?)\s*("
        + "|".join(re.escape(sym) for sym, _ in units)
        + r")(?!\w)",
    )


@functools.lru_cache(maxsize=1)
def _fi_unit_lookup() -> dict[str, str]:
    return {sym: word for sym, word in _fi_unit_map()}


# Dollar sign precedes the number: $5 → 5 dollaria
_FI_DOLLAR_RE = re.compile(r"\$\s*(\d+(?:[.,]\d+)?)")

# Section sign (§) precedes the number: § 242 → pykälä 242. The output
# `pykälä` then acts as a before-governor for Pass G, which emits the
# number in nominative by default. In the rare case the surrounding
# prose already contains `pykälässä` or similar, both governors are
# visible to Pass G's ±3-word scan; the nearer one wins (typically the
# inserted `pykälä` at distance 1).
_FI_SECTION_SIGN_RE = re.compile(r"§\s*(?=\d)")


def _expand_unit_symbols(text: str) -> str:
    """Expand numeric unit symbols to Finnish partitive forms (Pass M).

    Keeps the digit token in place so Pass G's governor table can still
    detect the unit word and assign nominative case to the numeral.
    """
    # Dollar sign: prefix form — convert first.
    text = _FI_DOLLAR_RE.sub(r"\1 dollaria", text)

    # Section sign (§): prefix form — `§ 242` → `pykälä 242`.
    text = _FI_SECTION_SIGN_RE.sub("pykälä ", text)

    lookup = _fi_unit_lookup()

    def _unit_sub(m: re.Match) -> str:
        number = m.group(1)
        sym = m.group(2)
        word = lookup.get(sym, sym)
        return f"{number} {word}"

    return _fi_unit_re().sub(_unit_sub, text)


# Pass C: century/era expressions — digit + "-luku" declension suffix.
# Suffix list lives in data/fi_century_suffixes.yaml.
@functools.lru_cache(maxsize=1)
def _fi_century_suffixes() -> tuple[str, ...]:
    from src._yaml_data import load_yaml
    raw = load_yaml("fi_century_suffixes") or []
    return tuple(str(s) for s in raw)


@functools.lru_cache(maxsize=1)
def _fi_century_re() -> re.Pattern[str]:
    suffixes = _fi_century_suffixes()
    if not suffixes:
        return re.compile(r"(?!x)x")
    return re.compile(r"(\d+)-(" + "|".join(suffixes) + r")\b")

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
# (`5 prosenttia`, `3 kertaa`). All keys are lowercase. The tables live
# in data/fi_governors.yaml and are the machine-readable form of
# `docs/finnish_governor_cases.md` and `docs/finnish_normalizer_design.md`
# §1. Add new entries by lemma, not by surface form — morphology is
# handled by listing each case form we care about as its own key.
@functools.lru_cache(maxsize=1)
def _fi_governors() -> tuple[dict[str, str], dict[str, str], frozenset[str]]:
    from src._yaml_data import load_yaml
    raw = load_yaml("fi_governors") or {}
    before = {str(k): str(v) for k, v in (raw.get("before") or {}).items()}
    after = {str(k): str(v) for k, v in (raw.get("after") or {}).items()}
    year_g = frozenset(str(w) for w in (raw.get("year_governors") or []))
    return before, after, year_g



# Pass L: Roman numeral expansion.
#
# Matches sequences of 2+ Roman-numeral capital letters at word boundaries.
# The lookahead `(?=[IVXLCDM]{2,}\b)` excludes standalone `I`, `V`, `X`, etc.
# so the English pronoun "I" and single-letter abbreviations are never touched.
_FI_ROMAN_RE = re.compile(
    r"\b(?=[IVXLCDM]{2,}\b)(M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))\b"
)

# Roman numeral context sets — vocabulary lives in data/fi_roman_contexts.yaml.
#
# blacklist         — modern acronyms that also look Roman (DC, LCD, ...).
# regnal_names      — royal first names (ordinal reading when they precede).
# titles            — regnal/ecclesial titles (ordinal reading when they precede).
# ordinal_before    — section keywords (ordinal when before).
# ordinal_after     — century/chapter keywords (ordinal when after).
@functools.lru_cache(maxsize=1)
def _fi_roman_contexts() -> dict[str, frozenset[str]]:
    from src._yaml_data import load_yaml
    raw = load_yaml("fi_roman_contexts") or {}
    return {
        "blacklist": frozenset(str(w) for w in (raw.get("blacklist") or [])),
        "regnal_names": frozenset(
            str(w) for w in (raw.get("regnal_names") or [])
        ),
        "titles": frozenset(str(w) for w in (raw.get("titles") or [])),
        "ordinal_before": frozenset(
            str(w) for w in (raw.get("ordinal_before") or [])
        ),
        "ordinal_after": frozenset(
            str(w) for w in (raw.get("ordinal_after") or [])
        ),
    }


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
    ctx = _fi_roman_contexts()

    for m in _FI_ROMAN_RE.finditer(text):
        token_str = m.group(0)
        # Blacklist check (case-sensitive on upper form).
        if token_str.upper() in ctx["blacklist"]:
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
                before_words[0] in ctx["regnal_names"]
                or before_words[0] in ctx["titles"]
            ):
                is_ordinal = True
            # Section keyword before → ordinal.
            elif before_words and before_words[0] in ctx["ordinal_before"]:
                is_ordinal = True
            # Century/chapter keyword after → ordinal.
            elif after_words and after_words[0] in ctx["ordinal_after"]:
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
    before, after, year_governors = _fi_governors()

    # Nearest "before" governor wins.
    for lemma in _word_iter(idx - 1, -1):
        if lemma in before:
            if year_shortening == "radio" and is_year \
                    and lemma in year_governors:
                return "nominative"
            return before[lemma]

    # Otherwise look for a partitive-head "after" governor.
    for lemma in _word_iter(idx + 1, 1):
        if lemma in after:
            return after[lemma]

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

    Pass ordering invariants:
        The passes below run in a fixed sequence. Each bullet explains WHY
        the earlier pass must finish before the later one — reorder any of
        them and the later pass silently produces wrong output. These are
        load-bearing; do not shuffle them without updating this list.

        - O must run first: Pass O strips emoji codepoints. Done before
          everything else so no later regex has to consider whether a
          random pictograph could fall inside its character class. Cheap,
          and isolates emoji handling to one place.
        - T must run before C, D, F, G: Pass T expands `D.M.YYYY` dates
          and `klo HH:MM` clock times into spoken form. Without it, F
          would misread `14.4.2026` as the decimal `14.4` followed by
          another decimal `.2026`, and G would expand each digit run as
          an independent cardinal. Run after K so abbreviation periods
          (which T does not touch) are already gone.
        - K must run before C, D, F, G: Pass K expands abbreviations like
          ``esim.`` / ``ks.`` whose trailing periods would otherwise look
          like sentence-terminal dots to the later passes. Finish the
          abbreviations first, then the later passes only see real periods.
        - K must run before L: Pass L's Roman-numeral detector uses
          surrounding word context (``luku XIV``) to decide ordinal vs.
          cardinal. If abbreviation periods are still present they can
          shift the tokenizer's view of "the word before/after" and the
          classifier picks the wrong form.
        - L must run before M: Pass M rewrites ``5 %`` as ``5 prosenttia``
          and injects partitive head nouns. If Roman numerals were still
          around, a token like ``XIV %`` would leave the Roman pass with a
          head noun it never expected. Roman first, then units.
        - M must run before D: Pass M emits the governor words
          (``prosenttia``, ``kertaa``, ``euroa``) that Pass G will later
          use to pick case. If D ran first it would split any number range
          neighboring a unit symbol before M could attach the governor,
          and G would miss the case signal.
        - M must run before F: Pass M expects bare ``5,0 %`` forms so it
          can rewrite the unit symbol. If F ran first and converted the
          decimal to ``viisi pilkku nolla``, the digit prefix M needs to
          anchor on is gone.
        - M must run before G: this is the load-bearing one — M writes the
          governor word (``prosenttia``) right next to the digit. Pass G's
          ±3-word scan then spots that governor and emits the number in
          the correct case. Flip the order and G sees a naked digit with
          no governor nearby, falls back to nominative, and readers hear
          ungrammatical Finnish.
        - C must run before D and G: Pass C owns century expressions like
          ``1500-luvulla``. It eats the digit together with its suffix. If
          D ran first it would see ``1500-luvulla`` as a range (``1500``
          to ``luvulla``) and split it wrong. If G ran first the bare
          ``1500`` would be read as a cardinal before C got a chance.
        - E must run before G: Pass E rewrites ``s. 42`` as ``sivu 42`` so
          Pass G's governor table can match ``sivu`` and inflect the
          digit. Without E the abbreviation never becomes the governor
          word G needs.
        - D must run before F: Pass D normalizes ranges like ``42-45`` by
          replacing the dash with a space. If F ran first the dot/comma
          inside ``3,14`` is fine, but if a range contained a decimal
          endpoint the regex tangles with F's decimal matcher. D first
          keeps the two concerns separate.
        - F must run before G: Pass F consumes any digit-dot-digit
          pattern. Pass G's cardinal regex would otherwise eat the whole
          number and the fractional digits would vanish. Decimals first,
          cardinals second.
        - I and H must run after G: Pass I (loanword respelling) and
          Pass H (compound-number morpheme split) both operate on the
          num2words output produced by Pass G. They need the expanded
          Finnish word form, not the raw digits, so they can only run
          once G has already written words to the text.
        - I must run before H: Pass I may insert its own hyphens (e.g.
          ``instituu-tio``). Running H first on a compound that later
          gets respelled would double-split the token. Loanwords first,
          morpheme boundaries second.

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

    # Pass O — strip emoji. Runs first so no later regex needs to worry
    # about pictographs sneaking through character classes.
    text = _strip_emoji(text)

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

    # Pass T — date and clock-time expansion. Runs after K (so any `klo.`
    # abbreviation form is already `kello`, although the time regex also
    # accepts the bare `klo` form) and before C/D/F/G (whose digit
    # handlers would otherwise mangle `14.4.2026` and `klo 20:30`).
    text = _expand_dates_and_times(text)

    # Pass L — Roman numerals (regnal ordinals, chapter ordinals, cardinal fallback).
    # Runs after Pass K (abbreviation expansion) so periods in abbreviations
    # don't bleed into Roman numeral detection; before Pass M so unit expansion
    # doesn't consume context the ordinal classifier needs.
    text = _expand_roman_numerals(text)
    # Pass N — acronym expansion (known whitelist, exact-case) followed
    # by the letter-by-letter fallback for unknown all-caps tokens.
    text = _expand_acronyms(text)
    text = _expand_acronym_fallback(text)

    # Pass M — measurement unit / currency symbol expansion. Must run
    # before Pass D/F/G so the digit prefix stays intact for governor
    # detection (e.g. `5 prosenttia` → Pass G picks nominative).
    text = _expand_unit_symbols(text)

    # Pass C — century expressions.
    def _century_sub(m: re.Match) -> str:
        return f"{_w(int(m.group(1)))} {m.group(2)}"

    text = _fi_century_re().sub(_century_sub, text)

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
    text = _FI_WHITESPACE_CLEANUP_RE.sub(" ", text)
    text = _FI_SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    return text
