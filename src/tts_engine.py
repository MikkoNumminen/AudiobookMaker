"""TTS engine module for AudiobookMaker.

Converts text to speech using edge-tts and combines audio chunks with pydub.
Supports Finnish and English voices with configurable speed.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from pydub import AudioSegment

# NOTE: edge_tts is imported lazily inside _synthesize_chunk() so that
# other consumers (e.g. dev_qwen_tts.py) can `from src.tts_engine import
# split_text_into_chunks` without dragging in an online-TTS dependency
# they don't need.


# ---------------------------------------------------------------------------
# Voice configuration
# ---------------------------------------------------------------------------

# voice_id -> display name shown in the GUI
VOICE_DISPLAY_NAMES: dict[str, str] = {
    "fi-FI-NooraNeural": "Noora (suomi, nainen)",
    "fi-FI-HarriNeural": "Harri (suomi, mies)",
    "en-US-JennyNeural": "Jenny (English US, female)",
    "en-US-AriaNeural": "Aria (English US, female)",
    "en-US-AvaNeural": "Ava (English US, female)",
    "en-US-GuyNeural": "Guy (English US, male)",
    "en-US-AndrewNeural": "Andrew (English US, male)",
    "en-GB-SoniaNeural": "Sonia (English GB, female)",
    "en-GB-RyanNeural": "Ryan (English GB, male)",
}

VOICES: dict[str, dict[str, str]] = {
    "fi": {
        "default": "fi-FI-NooraNeural",
        "Noora (suomi, nainen)": "fi-FI-NooraNeural",
        "Harri (suomi, mies)": "fi-FI-HarriNeural",
    },
    "en": {
        "default": "en-US-JennyNeural",
        "Jenny (English US, female)": "en-US-JennyNeural",
        "Aria (English US, female)": "en-US-AriaNeural",
        "Ava (English US, female)": "en-US-AvaNeural",
        "Guy (English US, male)": "en-US-GuyNeural",
        "Andrew (English US, male)": "en-US-AndrewNeural",
        "Sonia (English GB, female)": "en-GB-SoniaNeural",
        "Ryan (English GB, male)": "en-GB-RyanNeural",
    },
}

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

# Lookup dict for unit expansion (symbol → Finnish word).
_FI_UNIT_LOOKUP: dict[str, str] = {sym: word for sym, word in _FI_UNIT_MAP}


def _expand_unit_symbols(text: str) -> str:
    """Expand numeric unit symbols to Finnish partitive forms (Pass M).

    Keeps the digit token in place so Pass G's governor table can still
    detect the unit word and assign nominative case to the numeral.
    """
    # Dollar sign: prefix form — convert first.
    text = _FI_DOLLAR_RE.sub(r"\1 dollaria", text)

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

# Pass D: numeric ranges like "1500-1800" or "1100–1300".
_FI_RANGE_RE = re.compile(r"(\d{3,4})\s*[-–]\s*(\d{3,4})\b")

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
    # Page governors.
    "sivu": "nominative",
    "sivut": "nominative",
    "sivulla": "adessive",
    "sivulta": "ablative",
    "sivulle": "allative",
    "sivuilla": "adessive",
    "sivusta": "elative",
    "sivuun": "illative",
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
    # Row / line positions.
    "rivi": "nominative",
    "rivillä": "adessive",
    "riviltä": "ablative",
    "riville": "allative",
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

# Pass H: split glued Finnish compound-number morphemes.
#
# num2words 0.5.14 emits Finnish compound numbers with the hundreds and
# tens morphemes glued to whatever follows — e.g. 1889 nominative
# "tuhat kahdeksansataakahdeksankymmentäyhdeksän". Chatterbox-TTS then
# tokenizes the glued word as one giant token and mispronounces it.
# We insert a space after every case-inflected form of `sata` and
# `kymmenen` when another morpheme is glued on. Standalone teens like
# "viisitoista" (15) are unaffected — they do not contain these stems.
_FI_MORPHEME_STEMS: tuple[str, ...] = (
    # "sata" case forms (singular).
    "sataa", "sadan", "sadassa", "sadasta", "sataan",
    "sadalla", "sadalta", "sadalle", "satana", "sadaksi",
    "sadaksi", "satoja",
    # "kymmenen" case forms.
    "kymmentä", "kymmenen", "kymmenessä", "kymmenestä",
    "kymmeneen", "kymmenellä", "kymmeneltä", "kymmenelle",
    "kymmenenä", "kymmeneksi",
)
_FI_MORPHEME_BOUNDARY_RE = re.compile(
    r"(" + "|".join(sorted(set(_FI_MORPHEME_STEMS), key=len, reverse=True))
    + r")(?=[a-zäöå])"
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


def normalize_finnish_text(
    text: str,
    drop_citations: bool = True,
    year_shortening: str = "radio",
) -> str:
    """Expand Finnish-specific patterns so TTS engines read them correctly.

    Rewrites numbers, century expressions, numeric ranges, page abbreviations,
    and elided-hyphen compounds into plain word-form Finnish. Uses num2words
    (lazy import) for the actual digit → word conversion with
    ``case=`` set from governor-word detection (±3 word tokens of
    context). If num2words is not installed the function degrades
    gracefully and returns the input unchanged.

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

    # Pass A — drop bibliographic citations.
    if drop_citations:
        text = _FI_CITE_RE.sub("", text)

    # Pass B — elided-hyphen compounds (just insert a space).
    text = _FI_ELIDED_HYPHEN_RE.sub(r"\1- \2", text)

    # Pass K — Finnish abbreviation expansion. Must run before Pass C so
    # abbreviation periods do not interfere with period-sensitive patterns.
    text = _expand_abbreviations(text)

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

    # Pass H — split glued compound-number morphemes (post num2words).
    text = _fi_split_number_compounds(text)

    # Collapse whitespace introduced by deletions/substitutions.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([.,;:!?])", r"\1", text)
    return text


# Maximum characters per TTS request. edge-tts has no hard limit but
# large chunks cause timeouts; 3000 chars is reliable in practice.
MAX_CHUNK_CHARS = 3000

# Sentence-ending punctuation used for smart splitting
_SENTENCE_END = {".", "!", "?", "…", "。"}

# Common Finnish/English abbreviations that end in a period but do NOT
# mark the end of a sentence. Matched case-insensitively on the token
# immediately before the period.
_ABBREVIATIONS = {
    # Finnish
    "esim", "ks", "mm", "ym", "yms", "n", "s", "v", "ts", "eli",
    "nk", "ns", "ko", "ao", "ed", "jne", "tms", "vrt", "huom",
    "mr", "mrs", "prof", "tri", "fil", "dos", "toim",
    # English
    "etc", "ie", "eg", "mr", "mrs", "ms", "dr", "vs", "cf", "no",
    "vol", "pp", "p", "ch", "fig", "ed", "al",
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class TTSConfig:
    """Configuration for a TTS conversion job."""

    language: str = "fi"
    voice: str = ""          # empty = use language default
    rate: str = "+0%"        # e.g. "+10%", "-20%"
    volume: str = "+0%"
    normalize_text: bool = True
    """If True and language == "fi", run the input through
    :func:`normalize_finnish_text` before chunking. This expands years,
    century expressions, numeric ranges, and elided-hyphen compounds into
    word-form Finnish so every engine (Edge-TTS, Piper, Chatterbox, ...)
    pronounces them correctly. Set False to pass raw text through."""

    year_shortening: str = "radio"
    """Controls how 4-digit Finnish year literals are read aloud. The
    default ``"radio"`` follows the Kielikello radio-announcer
    convention where years stay in nominative regardless of the
    governing preposition (`vuodesta 1917` → "vuodesta tuhat
    yhdeksänsataa seitsemäntoista"). Set to ``"full"`` to emit full
    case agreement per VISK §772 (`vuodesta 1917` → "vuodesta
    tuhannesta yhdeksästäsadastaseitsemästätoista"). Only affects
    years in the 1000–2100 range; other integers always follow the
    governor-word table in :mod:`src.tts_engine`."""

    def resolved_voice(self) -> str:
        if self.voice:
            return self.voice
        lang = self.language if self.language in VOICES else "fi"
        return VOICES[lang]["default"]


ProgressCallback = Callable[[int, int, str], None]
"""Callback(current_chunk, total_chunks, status_message)."""


# ---------------------------------------------------------------------------
# Text chunking
# ---------------------------------------------------------------------------


def split_text_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split text into chunks of at most max_chars characters.

    Splits on sentence boundaries when possible to avoid breaking mid-sentence.

    Args:
        text: Input text to split.
        max_chars: Maximum characters per chunk.

    Returns:
        List of text chunks.
    """
    if not text.strip():
        return []

    chunks: list[str] = []
    current = ""

    # Split into sentences by walking character by character
    sentences = _split_sentences(text)

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # A single sentence longer than max_chars must be force-split
        if len(sentence) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            # Force-split on word boundaries
            chunks.extend(_force_split(sentence, max_chars))
            continue

        if len(current) + len(sentence) + 1 <= max_chars:
            current = current + " " + sentence if current else sentence
        else:
            if current:
                chunks.append(current.strip())
            current = sentence

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if c]


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, preserving punctuation.

    Handles the hard cases that a naive split-on-period-loses-to:
      * Abbreviations ("esim.", "ks.", "Mr.", "Dr.") — period does not end
        the sentence.
      * Numbered items and decimals ("1100-luvun", "5.2", "I.") — period
        followed by a digit or letter on the same token is not a sentence end.
      * Ellipsis ("...") — treated as a single terminator, not three.
      * A period is only a real sentence end when followed by whitespace and
        then an uppercase letter, digit-uppercase combination, or end of text.
    """
    if not text:
        return []

    sentences: list[str] = []
    n = len(text)
    start = 0
    i = 0
    while i < n:
        char = text[i]

        # Always treat ! and ? and … and 。 as hard sentence enders when
        # followed by whitespace or end of text.
        if char in {"!", "?", "…", "。"}:
            # Consume repeated punctuation (e.g. "?!").
            while i + 1 < n and text[i + 1] in {"!", "?", "…", "."}:
                i += 1
            if i + 1 >= n or text[i + 1].isspace():
                sentences.append(text[start : i + 1])
                i += 1
                # Skip whitespace
                while i < n and text[i].isspace():
                    i += 1
                start = i
                continue

        if char == ".":
            # Handle ellipsis "..."
            if i + 2 < n and text[i + 1] == "." and text[i + 2] == ".":
                i += 3
                if i >= n or text[i].isspace():
                    sentences.append(text[start:i])
                    while i < n and text[i].isspace():
                        i += 1
                    start = i
                    continue
                else:
                    continue

            # Look back at the token immediately before the period.
            token_start = i - 1
            while token_start >= start and not text[token_start].isspace():
                token_start -= 1
            token = text[token_start + 1 : i].lower()

            # Abbreviation?  Don't split.
            if token in _ABBREVIATIONS:
                i += 1
                continue

            # Single letter + period (initial like "H. Pihlajamäki") — don't split.
            if len(token) == 1 and token.isalpha():
                i += 1
                continue

            # Lookahead: is this really the end of a sentence?
            # A real sentence end is "."  followed by whitespace and then
            # an uppercase letter or a digit, or end of text.
            j = i + 1
            if j >= n:
                sentences.append(text[start : i + 1])
                start = n
                i = n
                break
            if not text[j].isspace():
                # e.g. "5.2" or "google.com" — not a sentence end.
                i += 1
                continue
            # Skip whitespace to find the next non-space character.
            k = j
            while k < n and text[k].isspace():
                k += 1
            if k >= n:
                sentences.append(text[start : i + 1])
                start = n
                i = n
                break
            # Accept if next char starts a new sentence-like token.
            if text[k].isupper() or text[k].isdigit() or text[k] in {'"', "'", "«", "("}:
                sentences.append(text[start : i + 1])
                i = k
                start = k
                continue
            # Otherwise (lowercase continuation) treat as inline period.
            i += 1
            continue

        i += 1

    if start < n:
        tail = text[start:]
        if tail.strip():
            sentences.append(tail)

    return sentences


def _force_split(text: str, max_chars: int) -> list[str]:
    """Split a long string on word boundaries."""
    words = text.split()
    chunks: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 <= max_chars:
            current = current + " " + word if current else word
        else:
            if current:
                chunks.append(current)
            current = word
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# edge-tts async core
# ---------------------------------------------------------------------------


async def _synthesize_chunk(
    text: str,
    voice: str,
    rate: str,
    volume: str,
    output_path: str,
) -> None:
    """Synthesize a single chunk to an MP3 file via edge-tts."""
    import edge_tts  # lazy import — see note at top of file

    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=volume)
    await communicate.save(output_path)


async def _synthesize_all_chunks(
    chunks: list[str],
    config: TTSConfig,
    tmp_dir: str,
    progress_cb: Optional[ProgressCallback],
) -> list[str]:
    """Synthesize all chunks sequentially and return list of MP3 file paths."""
    voice = config.resolved_voice()
    output_files: list[str] = []
    total = len(chunks)

    for i, chunk in enumerate(chunks):
        out_path = os.path.join(tmp_dir, f"chunk_{i:04d}.mp3")
        if progress_cb:
            progress_cb(i, total, f"Syntetisoidaan pala {i + 1}/{total}…")
        await _synthesize_chunk(chunk, voice, config.rate, config.volume, out_path)
        output_files.append(out_path)

    if progress_cb:
        progress_cb(total, total, "Yhdistetään äänitiedostot…")

    return output_files


# ---------------------------------------------------------------------------
# Audio combining
# ---------------------------------------------------------------------------


def _trim_chunk_silence(
    segment: AudioSegment,
    threshold_db: float = -45.0,
    keep_ms: int = 30,
) -> AudioSegment:
    """Trim leading and trailing silence from a synthesized chunk.

    edge-tts returns each chunk with ~150ms leading and ~800ms trailing
    silence.  Without trimming, concatenating 100+ chunks produces ~1 second
    of dead air at every chunk boundary, which sounds like the voice is
    cutting mid-sentence.

    Args:
        segment: Audio segment to trim.
        threshold_db: Anything quieter than this is considered silence.
        keep_ms: Amount of silence to keep at each edge for a natural edge.

    Returns:
        Trimmed audio segment.
    """
    from pydub.silence import detect_leading_silence

    lead = detect_leading_silence(segment, silence_threshold=threshold_db)
    trail = detect_leading_silence(segment.reverse(), silence_threshold=threshold_db)
    start = max(0, lead - keep_ms)
    end = len(segment) - max(0, trail - keep_ms)
    if end <= start:
        # Chunk was entirely silent — return it as-is to avoid a zero-length slice
        return segment
    return segment[start:end]


def combine_audio_files(
    input_paths: list[str],
    output_path: str,
    inter_chunk_pause_ms: int = 200,
) -> None:
    """Combine multiple MP3 files into one using pydub.

    Each chunk is trimmed of leading/trailing silence before concatenation so
    that the seams between chunks don't sound like dead air.  A short natural
    pause is inserted between chunks so the speech flow still feels paced.

    Args:
        input_paths: Ordered list of MP3 file paths to concatenate.
        output_path: Destination MP3 path.
        inter_chunk_pause_ms: Length of the synthetic pause inserted between
            adjacent chunks (milliseconds).

    Raises:
        ValueError: If input_paths is empty.
    """
    if not input_paths:
        raise ValueError("No audio files to combine.")

    gap = AudioSegment.silent(duration=inter_chunk_pause_ms)
    combined = AudioSegment.empty()
    for i, path in enumerate(input_paths):
        # Auto-detect format from the file extension so both edge-tts MP3
        # chunks and piper WAV chunks are supported.
        segment = _trim_chunk_silence(AudioSegment.from_file(path))
        combined += segment
        if i < len(input_paths) - 1:
            combined += gap

    combined.export(output_path, format="mp3")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def text_to_speech(
    text: str,
    output_path: str | Path,
    config: Optional[TTSConfig] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> None:
    """Convert text to an MP3 file.

    Splits text into chunks, synthesizes each with edge-tts, then combines.

    Args:
        text: Input text to convert.
        output_path: Destination MP3 file path.
        config: TTS configuration (voice, language, speed). Defaults to Finnish.
        progress_cb: Optional callback(current, total, message) for progress updates.

    Raises:
        ValueError: If text is empty.
        RuntimeError: If synthesis or audio combination fails.
    """
    if not text.strip():
        raise ValueError("Cannot synthesize empty text.")

    if config is None:
        config = TTSConfig()

    output_path = str(output_path)

    if config.normalize_text and config.language == "fi":
        text = normalize_finnish_text(
            text, year_shortening=config.year_shortening
        )

    chunks = split_text_into_chunks(text)

    if not chunks:
        raise ValueError("Text produced no chunks after splitting.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        mp3_files = asyncio.run(
            _synthesize_all_chunks(chunks, config, tmp_dir, progress_cb)
        )
        combine_audio_files(mp3_files, output_path)

    if progress_cb:
        progress_cb(len(chunks), len(chunks), "Valmis!")


def chapters_to_speech(
    chapters: list[tuple[str, str]],  # (title, content)
    output_dir: str | Path,
    config: Optional[TTSConfig] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> list[str]:
    """Convert multiple chapters to individual MP3 files.

    Args:
        chapters: List of (title, content) tuples.
        output_dir: Directory where MP3 files will be saved.
        config: TTS configuration.
        progress_cb: Optional progress callback.

    Returns:
        List of created MP3 file paths.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_files: list[str] = []
    total_chapters = len(chapters)

    for idx, (title, content) in enumerate(chapters):
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
        out_path = output_dir / f"{idx + 1:02d}_{safe_title}.mp3"

        chapter_total = len(split_text_into_chunks(content))

        def chapter_cb(current: int, total: int, msg: str) -> None:
            if progress_cb:
                # Map chapter progress into overall progress
                overall = idx * 100 + (current * 100 // max(total, 1))
                progress_cb(overall, total_chapters * 100, f"Luku {idx + 1}/{total_chapters}: {msg}")

        text_to_speech(content, out_path, config, chapter_cb if progress_cb else None)
        output_files.append(str(out_path))

    return output_files
