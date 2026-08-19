"""README quantitative claims must match the code they describe.

Prose claims drift silently — this session already found '400+ unit tests'
overstated (it was 338). Lock the load-bearing architectural claim that maps
cleanly to code: the Finnish normalizer's pass count. The passes are an
explicit lettered sequence (Pass A, B, C, ...) in src/tts_normalizer_fi.py, so
the count is derivable; if a pass is added/removed without updating the README
(or vice versa), this fails.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
NORMALIZER = REPO_ROOT / "src" / "tts_normalizer_fi.py"

_README_PASS_CLAIM = re.compile(r"(\d+)\s+normalization passes")

# Two pass-naming forms the first version of this regex (`\bPass ([A-Z])\b`)
# could not see, so it undercounted by two and locked a stale README number:
#
#   Pass J1 / J2 / J3  — a numbered sub-pass. The digit is a word character,
#                        so there is no \b after the letter and nothing matched.
#   Pass Å             — the minus-sign pass. Å is outside ASCII [A-Z].
#
# Both are applied in the pipeline like any other pass. The optional digit is
# consumed and not captured, so J1/J2/J3 collapse to a single "J".
_PASS_LETTER = re.compile(r"\bPass ([A-ZÅÄÖ])\d?\b")


def _code_pass_count() -> int:
    """Distinct lettered passes referenced in the normalizer source."""
    letters = set(_PASS_LETTER.findall(NORMALIZER.read_text(encoding="utf-8")))
    return len(letters)


def test_readme_normalizer_pass_count_matches_code() -> None:
    m = _README_PASS_CLAIM.search(README.read_text(encoding="utf-8"))
    assert m, "README no longer states an 'N normalization passes' claim to verify"
    claimed = int(m.group(1))
    actual = _code_pass_count()
    assert claimed == actual, (
        f"README claims {claimed} normalization passes but "
        f"src/tts_normalizer_fi.py defines {actual} distinct lettered passes. "
        f"Update whichever is stale."
    )
