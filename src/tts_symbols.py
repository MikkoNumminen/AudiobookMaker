"""Symbol handling shared by every language normalizer.

Why this module exists: a symbol character that survives normalization
reaches the synth glued to a word — ``0 to −90px`` normalizes to
``zero to −ninetypx``, and ``884×900`` to ``…neljä×yhdeksänsataa``.
Chatterbox cannot phonemize those glyphs, and the two languages fail
differently:

* English drops the glyph silently. ``0 to −90px at 884×900`` was read
  as "0 to 90 pecs at 884 900" — the minus sign gone, so a negative
  offset is narrated as a positive one. The text is wrong, and nothing
  in the pipeline notices.
* Finnish early-stops. The T3 decoder emits EOS at the unphonemizable
  token and the rest of the chunk is never spoken. Observed on a real
  conversion: one chunk lost its closing clause, and all five band-guard
  retries reproduced the same truncation because the cause is the input,
  not sampling noise.

The fix is two layers, because enumerating every glyph a document might
contain is not a thing anyone can finish:

1. :func:`expand_symbols` — a per-language table that turns the symbols
   that actually carry meaning into spoken words. Runs as a normal pass
   inside each language normalizer.
2. :func:`strip_unspeakable` — a catch-all that replaces every remaining
   Unicode *symbol* codepoint with a space, so an unknown glyph can never
   glue itself to a neighbouring word. The dispatcher applies this to
   every language unconditionally, so a future backend cannot forget it.

Layer 2 replaces rather than deletes: deleting ``a×b`` yields ``ab``, one
bogus word. Replacing yields ``a b``, two real ones.
"""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# Unicode general categories that no TTS engine can pronounce. Math (Sm),
# currency (Sc), modifier (Sk) and other (So) symbols all reach the
# phonemizer as an unmapped token.
#
# `No` (number, other) is in this set for a reason that is easy to miss:
# vulgar fractions (½ ⅓ ⅞) and superscript digits (² ³) are NOT symbols
# by Unicode's reckoning, they are numbers. Without `No` here the gate
# would wave through every fraction outside the table below — the exact
# bug this module exists to prevent, one category over. Ordinary digits
# are `Nd` and are never touched.
#
# Letters, marks, decimal digits, punctuation and separators are all
# speakable and are never touched here.
_UNSPEAKABLE_CATEGORIES = frozenset({"Sm", "Sc", "Sk", "So", "No"})

# Symbols owned by an earlier, context-aware pass. They are listed here
# only so the reader knows their absence from the tables below is
# deliberate, not an oversight:
#   %  °C  °F  €  $  £     -> Pass M (unit / currency expansion)
#   §                      -> Pass Z (legal citations, Finnish only)
# Each of those runs before the layer-2 gate, so a symbol that survives
# in a context its owning pass did not recognise still becomes a space
# rather than reaching the synth.

_SHARED_TABLE: dict[str, tuple[str, str]] = {
    # glyph: (english, finnish)
    "−": ("minus", "miinus"),                 # − MINUS SIGN
    "÷": ("divided by", "jaettuna"),          # ÷
    "±": ("plus or minus", "plus miinus"),    # ±
    "≈": ("approximately", "noin"),           # ≈
    "≠": ("not equal to", "eri suuri kuin"),  # ≠
    "≤": ("at most", "enintään"),             # ≤
    "≥": ("at least", "vähintään"),           # ≥
    "∞": ("infinity", "ääretön"),             # ∞
    "+": ("plus", "plus"),
    "=": ("equals", "yhtä kuin"),
    # Vulgar fractions and superscripts are Unicode *numbers* (category
    # No), not symbols. The gate covers the ones missing here; these are
    # the forms common enough in prose to deserve real words.
    "½": ("one half", "puoli"),
    "¼": ("one quarter", "neljäsosa"),
    "¾": ("three quarters", "kolme neljäsosaa"),
    "⅓": ("one third", "kolmasosa"),
    "⅔": ("two thirds", "kaksi kolmasosaa"),
    "⅛": ("one eighth", "kahdeksasosa"),
    "²": ("squared", "toiseen"),
    "³": ("cubed", "kolmanteen"),
}

# × is the one glyph whose reading depends on context. Between two
# numbers it is a dimension ("884 by 900"), everywhere else it is
# multiplication ("times"). Finnish says "kertaa" for both, but the
# split is kept symmetrical so the two languages stay readable side by
# side.
_TIMES = "×"
_DIMENSION_RE = re.compile(r"(?<=\d)\s*" + _TIMES + r"\s*(?=\d)")

_TIMES_WORDS = {"en": ("by", "times"), "fi": ("kertaa", "kertaa")}

# `5x` — the multiplier idiom of tech prose. The `x` is a letter, so the
# layer-2 gate cannot help: it survives normalization and glues onto the
# spelled-out number as the non-word `viisix` / `fivex`.
#
# The `x` must touch the digit. `3 ≤ x` is an algebraic variable and has
# to stay one.
_MULTIPLIER_RE = re.compile(r"(?<=\d)x\b")

_MULTIPLIER_WORDS = {"en": " times", "fi": " kertaa"}

# `3D`, `2D`, `4K` — a digit fused to a single capital. The number pass
# expands the digit and leaves the letter welded on, giving the non-words
# `threeD` / `kolmeD`. Narrated, the letter is simply swallowed: "solid 3D
# letters" was heard as "solid three letters", which is a different claim.
#
# Splitting them lets the digit expand normally and leaves the letter as
# its own token, read out as a letter. Restricted to ONE capital at a word
# boundary so model numbers and mixed-case identifiers are left alone.
_DIGIT_LETTER_RE = re.compile(r"(?<=\d)([A-Z])\b")

_MULTISPACE_RE = re.compile(r"[ \t]{2,}")


def expand_symbols(text: str, lang: str) -> str:
    """Replace meaning-carrying symbols with their spoken words.

    Substitutions are padded with spaces on both sides and the padding is
    collapsed afterwards. That is what keeps ``884×900`` from becoming the
    single unpronounceable token ``884by900``.

    Args:
        text: Text at an early stage of normalization.
        lang: ``"en"`` or ``"fi"``. Any other value returns ``text``
            unchanged, so layer 2 is left to catch what this pass cannot.

    Returns:
        Text with the tabled symbols expanded.
    """
    if not text or lang not in _TIMES_WORDS:
        return text

    idx = 0 if lang == "en" else 1

    dimension_word, times_word = _TIMES_WORDS[lang]
    text = _DIMENSION_RE.sub(f" {dimension_word} ", text)
    text = text.replace(_TIMES, f" {times_word} ")
    text = _MULTIPLIER_RE.sub(_MULTIPLIER_WORDS[lang], text)
    text = _DIGIT_LETTER_RE.sub(r" \1", text)

    for glyph, words in _SHARED_TABLE.items():
        if glyph in text:
            text = text.replace(glyph, f" {words[idx]} ")

    return _MULTISPACE_RE.sub(" ", text)


def strip_unspeakable(text: str, lang: str = "") -> str:
    """Replace every remaining unpronounceable symbol with a space.

    The final gate. Anything still carrying a Unicode symbol category at
    this point was not claimed by any expansion pass, which means the
    synth would receive a token it cannot phonemize — silently dropped in
    English, an early stop in Finnish. A space is always the safer
    reading, and the dropped glyphs are logged so a codepoint nobody
    anticipated shows up in the run log instead of in the audio.

    Args:
        text: Fully normalized text, straight from a language backend.
        lang: Language code, used only to label the log line.

    Returns:
        Text containing no Unicode symbol codepoints.
    """
    if not text:
        return text

    dropped: dict[str, int] = {}
    out: list[str] = []
    for ch in text:
        if unicodedata.category(ch) in _UNSPEAKABLE_CATEGORIES:
            dropped[ch] = dropped.get(ch, 0) + 1
            out.append(" ")
        else:
            out.append(ch)

    if not dropped:
        return text

    detail = ", ".join(
        f"U+{ord(ch):04X} {unicodedata.name(ch, '?')} x{n}"
        for ch, n in sorted(dropped.items(), key=lambda kv: -kv[1])
    )
    logger.warning(
        "[normalizer%s] dropped %d unpronounceable symbol(s): %s. "
        "Add a spoken form to src/tts_symbols.py if any of these should "
        "be read aloud.",
        f" {lang}" if lang else "",
        sum(dropped.values()),
        detail,
    )
    return _MULTISPACE_RE.sub(" ", "".join(out))
