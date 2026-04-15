"""Pass O — date expressions for the English TTS normalizer.

Converts common date formats into spoken English:

    Jan 5, 1901        -> "January fifth nineteen oh one"
    5 January 1901     -> "the fifth of January nineteen oh one"
    January 5, 1901    -> "January fifth nineteen oh one"
    1/5/2020           -> "January fifth twenty twenty"    (US: month first)
    2020-01-05         -> "January fifth twenty twenty"    (ISO)
    Jan 5              -> "January fifth"

Must run BEFORE Pass F (years) so the year is consumed together with the
rest of the date rather than half-converted on its own.
"""

from __future__ import annotations

import re

# Note: the helpers live in `src.tts_normalizer_en`, which itself imports
# `_pass_o_dates` from this module at top-level. To avoid a circular
# import, we defer the helper import until the function actually runs.

__all__ = ["_pass_o_dates"]


# Month abbreviation / full name -> canonical full name + month number.
_MONTHS: dict[str, tuple[str, int]] = {
    "january":   ("January", 1),
    "jan":       ("January", 1),
    "february":  ("February", 2),
    "feb":       ("February", 2),
    "march":     ("March", 3),
    "mar":       ("March", 3),
    "april":     ("April", 4),
    "apr":       ("April", 4),
    "may":       ("May", 5),
    "june":      ("June", 6),
    "jun":       ("June", 6),
    "july":      ("July", 7),
    "jul":       ("July", 7),
    "august":    ("August", 8),
    "aug":       ("August", 8),
    "september": ("September", 9),
    "sept":      ("September", 9),
    "sep":       ("September", 9),
    "october":   ("October", 10),
    "oct":       ("October", 10),
    "november":  ("November", 11),
    "nov":       ("November", 11),
    "december":  ("December", 12),
    "dec":       ("December", 12),
}

_MONTH_NUM_TO_NAME = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

# Alternation of all month spellings, longest-first so "September" wins
# over "Sep" and "Sept" over "Sep".
_MONTH_ALT = "|".join(
    sorted(_MONTHS, key=len, reverse=True)
)

# "January 5, 1901" / "Jan 5 1901" / "Jan 5" / "January 5th, 1901"
_DATE_MONTH_FIRST_RE = re.compile(
    r"\b(" + _MONTH_ALT + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
    r"(?:\s*,?\s*(\d{4}))?\b",
    flags=re.IGNORECASE,
)

# "5 January 1901" / "5th of January 1901" / "5 Jan"
_DATE_DAY_FIRST_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?"
    r"(" + _MONTH_ALT + r")\.?"
    r"(?:\s+(\d{4}))?\b",
    flags=re.IGNORECASE,
)

# ISO "2020-01-05"
_DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

# US-style "1/5/2020" or "01/05/2020" (month first). Year is required so
# we don't eat arbitrary "1/5" fractions.
_DATE_US_SLASH_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def _day_ordinal(day: int) -> str:
    from src.tts_normalizer_en import _cardinal_word, _ordinal_word
    if 1 <= day <= 31:
        return _ordinal_word(day)
    return _cardinal_word(day)


def _valid_day(day: int, month: int) -> bool:
    if not (1 <= month <= 12):
        return False
    max_day = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]
    return 1 <= day <= max_day


def _spoken_date(month_name: str, day: int, year: int | None,
                 day_first: bool) -> str:
    from src.tts_normalizer_en import _year_to_words
    day_w = _day_ordinal(day)
    if day_first:
        core = f"the {day_w} of {month_name}"
    else:
        core = f"{month_name} {day_w}"
    if year is not None:
        # num2words renders years like 1901 as "nineteen oh-one"; the
        # hyphen between "oh" and the unit digit reads weirdly for TTS,
        # so flatten just that one to a space. Compound tens like
        # "forty-five" keep their hyphen (standard English spelling).
        year_words = re.sub(r"\boh-", "oh ", _year_to_words(year))
        return f"{core} {year_words}"
    return core


def _pass_o_dates(text: str) -> str:
    """Expand date expressions into spoken English.

    Applied BEFORE Pass F so the year token gets consumed in context
    rather than half-expanded on its own.
    """

    # ISO yyyy-mm-dd first — it's the most unambiguous pattern.
    def repl_iso(m: re.Match[str]) -> str:
        year = int(m.group(1))
        month = int(m.group(2))
        day = int(m.group(3))
        if not _valid_day(day, month):
            return m.group(0)
        return _spoken_date(_MONTH_NUM_TO_NAME[month], day, year,
                            day_first=False)

    text = _DATE_ISO_RE.sub(repl_iso, text)

    # US mm/dd/yyyy.
    def repl_us(m: re.Match[str]) -> str:
        month = int(m.group(1))
        day = int(m.group(2))
        year = int(m.group(3))
        if not _valid_day(day, month):
            return m.group(0)
        return _spoken_date(_MONTH_NUM_TO_NAME[month], day, year,
                            day_first=False)

    text = _DATE_US_SLASH_RE.sub(repl_us, text)

    # "5 January 1901" / "5th of January".
    def repl_day_first(m: re.Match[str]) -> str:
        day = int(m.group(1))
        month_token = m.group(2).lower()
        year_tok = m.group(3)
        entry = _MONTHS.get(month_token)
        if entry is None:
            return m.group(0)
        month_name, month_num = entry
        if not _valid_day(day, month_num):
            return m.group(0)
        year = int(year_tok) if year_tok else None
        return _spoken_date(month_name, day, year, day_first=True)

    text = _DATE_DAY_FIRST_RE.sub(repl_day_first, text)

    # "January 5, 1901" / "Jan 5".
    def repl_month_first(m: re.Match[str]) -> str:
        month_token = m.group(1).lower()
        day = int(m.group(2))
        year_tok = m.group(3)
        entry = _MONTHS.get(month_token)
        if entry is None:
            return m.group(0)
        month_name, month_num = entry
        if not _valid_day(day, month_num):
            return m.group(0)
        year = int(year_tok) if year_tok else None
        return _spoken_date(month_name, day, year, day_first=False)

    text = _DATE_MONTH_FIRST_RE.sub(repl_month_first, text)

    return text
