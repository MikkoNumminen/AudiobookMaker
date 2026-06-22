"""Golden pins for the Chatterbox synthesis + assembly constants.

These constants ARE the audio quality contract: generation sampling
(temperature / cfg / repetition penalty / exaggeration), the band-guard
re-roll thresholds, and the seam/pause/silence assembly timings. They were
tuned by ear on real Finnish output. An accidental edit silently changes how
every audiobook sounds — and because the GUI and CLI both launch this one
runner script, a drift here ships to everyone at once.

This test makes any change to a tuned constant a DELIBERATE, reviewed update:
if you change a value on purpose, update the expected value here in the same
commit. If this test fails and you did NOT mean to touch pacing, you have a
regression. See docs/CONVENTIONS.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import generate_chatterbox_audiobook as gca  # type: ignore  # noqa: E402


# name -> tuned value. Keep this the single source of truth for the review gate.
_GOLDEN = {
    # generation sampling
    "FI_REPETITION_PENALTY": 1.5,
    "FI_TEMPERATURE": 0.5,
    "FI_EXAGGERATION": 0.5,
    "FI_CFG_WEIGHT": 0.3,
    # band guard (truncation / rambling re-roll)
    "MIN_AUDIO_S_PER_CHAR": 0.058,
    "MAX_AUDIO_S_PER_CHAR": 0.200,
    "MIN_AUDIO_RETRY_CHAR_FLOOR": 40,
    "MIN_AUDIO_MAX_RETRIES": 5,
    # chunking
    "CHUNK_MIN_CHARS": 60,
    # VAD edge padding
    "VAD_HEAD_PAD_MS": 100,
    "VAD_TAIL_PAD_MS": 500,
    # mid-phrase join keeps
    "MID_JOIN_TAIL_KEEP_MS": 70,
    "MID_JOIN_HEAD_KEEP_MS": 40,
    # inter-chunk seam pauses + internal-silence cap
    "CLAUSE_SEAM_GAP_MS": 150,
    "SENTENCE_SEAM_GAP_MS": 370,
    "MAX_INTERNAL_SILENCE_MS": 480,
}


def test_pacing_constants_match_golden() -> None:
    actual = {name: getattr(gca, name) for name in _GOLDEN}
    assert actual == _GOLDEN, (
        "A tuned Chatterbox pacing/generation constant changed. If this was "
        "deliberate, update _GOLDEN in this test in the same commit; otherwise "
        "you have an audio-quality regression. Changed: "
        + ", ".join(
            f"{k}={actual[k]!r} (expected {_GOLDEN[k]!r})"
            for k in _GOLDEN
            if actual[k] != _GOLDEN[k]
        )
    )
