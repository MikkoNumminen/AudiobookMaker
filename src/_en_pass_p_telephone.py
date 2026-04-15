"""Pass P: Telephone number normalization for English TTS.

Converts phone-number-shaped strings into digit-by-digit spoken form, with
commas between groups for natural prosody pauses.

Style (consistent across all supported formats):
- Each digit in a group is spoken individually: "1 2 3" -> "one two three".
- Groups (area code, prefix, line number) are separated by commas.
- A leading "+" becomes the word "plus".
- A leading country-code "1" stays as "one" (digit-by-digit, consistent with
  the rest). International prefixes like "+44" are spoken "plus four four".

Examples:
    (555) 123-4567        -> five five five, one two three, four five six seven
    555-123-4567          -> five five five, one two three, four five six seven
    1-800-555-1234        -> one, eight hundred, five five five, one two three four
                             (this implementation uses the all-digits form:
                              one, eight zero zero, five five five,
                              one two three four)
    +1 555 123 4567       -> plus one, five five five, one two three, four five six seven

Only clear phone-shaped patterns fire; arbitrary digit runs are left alone
for the cardinal pass.
"""

from __future__ import annotations

import re


def _digits_to_words(digits: str) -> str:
    """Spell each digit of `digits` as a lowercase word, space-separated."""
    # Lazy import to avoid circular-import issues when tts_normalizer_en is
    # still being constructed (it imports sibling pass modules at load time).
    from src.tts_normalizer_en import _cardinal_word

    return " ".join(_cardinal_word(int(d)) for d in digits)


# Patterns ordered most-specific first. Each is anchored on non-digit
# boundaries (lookaround) so that longer number runs (e.g. 12345678901234)
# are not partially consumed.
_NONDIGIT_LEFT = r"(?<!\d)"
_NONDIGIT_RIGHT = r"(?!\d)"

# International: +CC sep NNN sep NNN sep NNNN
_RE_INTL = re.compile(
    _NONDIGIT_LEFT
    + r"\+(\d{1,3})[-\s](\d{3})[-\s](\d{3})[-\s](\d{4})"
    + _NONDIGIT_RIGHT
)

# US with country code 1: 1 sep NNN sep NNN sep NNNN
_RE_US_CC = re.compile(
    _NONDIGIT_LEFT
    + r"1[-\s](\d{3})[-\s](\d{3})[-\s](\d{4})"
    + _NONDIGIT_RIGHT
)

# Parenthesised area code: (NNN) optional-space NNN sep NNNN
_RE_PARENS = re.compile(
    _NONDIGIT_LEFT
    + r"\((\d{3})\)\s*(\d{3})[-\s](\d{4})"
    + _NONDIGIT_RIGHT
)

# Plain US 10-digit: NNN sep NNN sep NNNN
_RE_US_10 = re.compile(
    _NONDIGIT_LEFT
    + r"(\d{3})[-\s](\d{3})[-\s](\d{4})"
    + _NONDIGIT_RIGHT
)


def _pass_p_telephone(text: str) -> str:
    """Convert phone-number strings into digit-by-digit spoken form."""
    if not text:
        return text

    def repl_intl(m: re.Match[str]) -> str:
        cc, a, b, c = m.group(1), m.group(2), m.group(3), m.group(4)
        return (
            f"plus {_digits_to_words(cc)}, "
            f"{_digits_to_words(a)}, "
            f"{_digits_to_words(b)}, "
            f"{_digits_to_words(c)}"
        )

    def repl_us_cc(m: re.Match[str]) -> str:
        a, b, c = m.group(1), m.group(2), m.group(3)
        return (
            f"one, "
            f"{_digits_to_words(a)}, "
            f"{_digits_to_words(b)}, "
            f"{_digits_to_words(c)}"
        )

    def repl_parens(m: re.Match[str]) -> str:
        a, b, c = m.group(1), m.group(2), m.group(3)
        return (
            f"{_digits_to_words(a)}, "
            f"{_digits_to_words(b)}, "
            f"{_digits_to_words(c)}"
        )

    def repl_us_10(m: re.Match[str]) -> str:
        a, b, c = m.group(1), m.group(2), m.group(3)
        return (
            f"{_digits_to_words(a)}, "
            f"{_digits_to_words(b)}, "
            f"{_digits_to_words(c)}"
        )

    # Order matters: international first (has leading +), then 1-prefixed,
    # then parenthesised, then plain 10-digit.
    text = _RE_INTL.sub(repl_intl, text)
    text = _RE_US_CC.sub(repl_us_cc, text)
    text = _RE_PARENS.sub(repl_parens, text)
    text = _RE_US_10.sub(repl_us_10, text)
    return text
