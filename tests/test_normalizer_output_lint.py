"""Lint normalizer OUTPUT for tokens no human would say.

Every normalizer bug found while narrating a real corpus shared one
shape — a token that survives normalization in a form nobody would read
aloud:

    gapage   nolla:sta   threeD   viisix   MPthree   Dminus
    one:five   yksi:viisi   nollas   kolme pilkku kaksi.nolla

None of them changes how long the audio is, so no duration check can
see them. Each was found by transcribing rendered audio and reading it
against the source — roughly twenty GPU-minutes per bug. Checking the
text transformation directly finds the same class in milliseconds.

This test is that check, run against a synthetic corpus of the shapes
that real technical prose actually contains.

**What it cannot catch.** Most of the examples above are pure letters —
`gapage`, `threeD`, `nollas` — and no structural rule tells those from
real words. Detecting them needs a dictionary per language, which this
does not attempt. What is detectable is a token carrying a structural
marker: a digit touching a letter, a colon or period inside a word, or
a codepoint the gate should have removed. That is a net under one class
of failure, not a proof of correctness.

It still earns its place: on its first run it caught `12:30 in the
afternoon` being read as "twelve:thirty inches the afternoon", because
the `in` unit was consuming the preposition.

The corpus is deliberately synthetic. Fixtures in this repo never use
third-party material.
"""

from __future__ import annotations

import re
import unicodedata

import pytest

from src.tts_normalizer import normalize_text
from src.tts_symbols import _UNSPEAKABLE_CATEGORIES

# ---------------------------------------------------------------------------
# The corpus — shapes that broke something, plus ordinary prose
# ---------------------------------------------------------------------------

CORPUS_EN = [
    "The site has one sound button, and nothing new to click.",
    "It lifts by 0 to −90px at 884×900, identical to a session with none set.",
    "Stepping the last 200px 20px at a time gives exactly −20px per 20px.",
    "The ceiling on savings is now 5x, and only for the cheaper tier.",
    "My name stood at the top as solid 3D letters on a 4K screen.",
    "Computed transition-duration is 0s in both places.",
    "Cost as delegated, upper bound: $2.08. All at list rates, $8.10.",
    "It cost $1.5M this year, a $25M round, and 2.5M USD before that.",
    "The input:output price ratio is 1:5, and 1 in 7 was rejected.",
    "Version 3.20.0 shipped after 1.2.3, on pages 42-45 of the notes.",
    "COVID-19 and the T-1000 are hyphenated, but -19 is negative.",
    "A 50/50 split, 1/2 of the total, and 15/75 pricing.",
    "Reading the status as JSON instead of matching on printed text.",
    "The WCAG boundary is interpretive, not settled.",
    "See p. 42 and pp. 12 for the gap. Changing pages never turns it off.",
    "Wake up. Then map. Then stop. Every word here ends in p.",
    "It was 2026-07-31, a Tuesday, at 12:30 in the afternoon.",
    "Half of 24,000 particles, and 323,826 tokens in total.",
]

CORPUS_FI = [
    "Sivustolla on yksi äänipainike, eikä mitään uutta tarvitse klikata.",
    "Nousee 0:sta −90 pikseliin koossa 884×900, samoin kuin istunnossa.",
    "Katto on nyt 5x, ja vain halvemmalla tasolla.",
    "Nimeni seisoi ylhäällä paksuina 3D-kirjaimina, kromin näköisenä.",
    "Laskettu transition-duration on 0s molemmissa paikoissa.",
    "Kustannus delegoituna, yläraja: 2,08 $. Samat tokenit, yläraja: 8,10 $.",
    "Budjetti oli 1,5 M€ ja palkka 500 k€.",
    "Suhde on 1:5, ja 1:7 on siedettävä. Arvo kasvoi 0:sta 90:een.",
    "Versio 3.20.0 julkaistiin, ja sitä ennen 1.2.3. Tehtiin 3.5.2026.",
    "COVID-19 ja T-1000 ovat yhdysmerkillisiä, mutta -19 on negatiivinen.",
    "Aukko on kirjattu README:en, ja ID:llä on merkitystä.",
    "Kenttä on 24 000 partikkelia, yhteensä 231 369 tokenia.",
    "Luettuna JSON-muodossa printatun tekstin sijaan.",
    "Vuosina 1914-1918 ja sivuilla 42-45 tapahtui paljon.",
    "1500-luvulla kirjoitettiin toisin kuin 20:een asti.",
]

# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

_TRAILING = "\"'(),.!?;:—–…«»"

CHECKS: list[tuple[str, re.Pattern[str], str]] = [
    ("digit welded before letters", re.compile(r"^\d+[A-Za-zÄÖÅäöå]+$"),
     "the number pass left a letter on a numeral (threeD, viisix)"),
    ("digit welded after letters", re.compile(r"^[A-Za-zÄÖÅäöå]+\d+$"),
     "the mirror case (MPthree, Aneljä)"),
    ("digits inside a word", re.compile(r"^[A-Za-zÄÖÅäöå]+\d+[A-Za-zÄÖÅäöå]+$"),
     "a number expanded inside an identifier"),
    ("colon inside a word", re.compile(r"^[A-Za-zÄÖÅäöå]+:[A-Za-zÄÖÅäöå]+$"),
     "an unexpanded clitic or ratio (nolla:sta, one:five)"),
    ("period inside a word", re.compile(r"^[A-Za-zÄÖÅäöå]{2,}\.[A-Za-zÄÖÅäöå]{2,}$"),
     "an abbreviation period left mid-token"),
]


def lint(text: str) -> list[str]:
    """Return a finding per token that no reader could pronounce."""
    findings = []
    for token in text.split():
        clean = token.strip(_TRAILING)
        if not clean:
            continue
        for name, pattern, why in CHECKS:
            if pattern.match(clean):
                findings.append(f"{name}: {clean!r} — {why}")

    for ch in set(text):
        if unicodedata.category(ch) in _UNSPEAKABLE_CATEGORIES:
            findings.append(
                f"unspeakable codepoint: U+{ord(ch):04X} "
                f"{unicodedata.name(ch, '?')} — the gate should have removed it"
            )
    return findings


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sentence", CORPUS_EN)
def test_english_output_has_no_unsayable_token(sentence):
    findings = lint(normalize_text(sentence, "en"))
    assert not findings, (
        f"{sentence!r}\n  -> {normalize_text(sentence, 'en')!r}\n  "
        + "\n  ".join(findings)
    )


@pytest.mark.parametrize("sentence", CORPUS_FI)
def test_finnish_output_has_no_unsayable_token(sentence):
    findings = lint(normalize_text(sentence, "fi"))
    assert not findings, (
        f"{sentence!r}\n  -> {normalize_text(sentence, 'fi')!r}\n  "
        + "\n  ".join(findings)
    )


# ---------------------------------------------------------------------------
# The linter has to actually detect the historical bugs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad,expected", [
    ("the 3D letters", "digit welded before letters"),
    ("a MP3 file", "digit welded after letters"),
    ("an abc123def token", "digits inside a word"),
    ("value nolla:sta here", "colon inside a word"),
    ("read gap.age aloud", "period inside a word"),
])
def test_linter_catches_the_shapes_it_exists_for(bad, expected):
    """A check that never fires is indistinguishable from a check that
    is broken. Each pattern is exercised against a token of its shape."""
    findings = lint(bad)
    assert any(expected in f for f in findings), findings


def test_linter_cannot_see_letters_only_glue():
    """Stated as a test so the limitation is not mistaken for coverage.

    `threeD` and `gapage` are exactly the bugs this file was written
    after, and no structural rule separates them from real words. A
    reader who assumes a green run means "no glued tokens" would be
    wrong in the direction that matters.
    """
    assert lint("the threeD letters") == []
    assert lint("without a gapage changing") == []


def test_linter_catches_a_leftover_symbol():
    findings = lint("a → b")
    assert any("U+2192" in f for f in findings)


def test_linter_is_quiet_on_ordinary_prose():
    """False positives make the gate useless; nobody keeps a noisy test."""
    assert lint("This is ordinary prose, with punctuation: and numbers.") == []
    assert lint("Tavallista suomea, jossa on välimerkkejä: ja numeroita.") == []


def test_linter_accepts_a_spelled_out_acronym():
    """`W C A G` is correct output, not a defect.

    Letter-by-letter spelling is how an unknown acronym is meant to be
    read, so it must not be flagged — a rule that fires on correct
    output is worse than no rule.
    """
    assert lint("the W C A G boundary is interpretive") == []
