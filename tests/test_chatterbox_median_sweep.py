"""Tests for Pass R — the relative (median) sweep in the Chatterbox runner.

The absolute band guard uses a fixed floor, MIN_AUDIO_S_PER_CHAR, which
has to stay low enough to be safe for the fastest text the project
narrates. That makes it blind to the common case: on a conversion whose
healthy rate was 0.070 s/char, nine of sixty-four chunks lost their
closing clause and six of those nine were never retried, because 0.060
clears a 0.058 floor while being obviously wrong next to its neighbours.

Pass R compares each chunk against the median of its own chapter. These
tests cover the selection rule, which is pure arithmetic — no torch, no
CUDA, no audio.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import generate_chatterbox_audiobook as gca  # type: ignore  # noqa: E402


def _chunks(rate, n, chars=200):
    """n chunks all narrated at the same rate."""
    return [(i, chars, rate * chars) for i in range(n)]


# ---------------------------------------------------------------------------
# The case the absolute floor misses
# ---------------------------------------------------------------------------

def test_catches_a_chunk_the_absolute_floor_would_wave_through():
    """The whole reason this pass exists.

    0.060 s/char clears MIN_AUDIO_S_PER_CHAR (0.058), so the band guard
    never retries it — but against a chapter running at 0.070 it is short
    by 14%, which in practice was a dropped clause.
    """
    rates = _chunks(0.070, 15)
    rates[7] = (7, 200, 0.060 * 200)

    assert 0.060 > gca.MIN_AUDIO_S_PER_CHAR, "premise: the old guard is happy"

    median, suspects = gca._median_sweep_candidates(rates)

    assert median == pytest.approx(0.070, abs=1e-6)
    assert [chi for chi, _, _ in suspects] == [7]


def test_a_uniformly_fast_chapter_has_no_suspects():
    """A fast reading is not a broken one. Only the odd chunk out counts."""
    median, suspects = gca._median_sweep_candidates(_chunks(0.052, 20))
    assert suspects == []
    assert median == pytest.approx(0.052, abs=1e-6)


def test_worst_offender_comes_first():
    rates = _chunks(0.070, 15)
    rates[3] = (3, 200, 0.060 * 200)
    rates[9] = (9, 200, 0.045 * 200)
    rates[11] = (11, 200, 0.055 * 200)

    _, suspects = gca._median_sweep_candidates(rates)

    assert [chi for chi, _, _ in suspects] == [9, 11, 3]


def test_a_chunk_just_inside_the_threshold_is_left_alone():
    rates = _chunks(0.070, 15)
    rates[2] = (2, 200, 0.070 * gca.MEDIAN_SWEEP_REL_FLOOR * 1.01 * 200)

    _, suspects = gca._median_sweep_candidates(rates)

    assert suspects == []


# ---------------------------------------------------------------------------
# Refusing to guess
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n", [0, 1, 3, gca.MEDIAN_SWEEP_MIN_CHUNKS - 1])
def test_too_few_chunks_to_have_a_median(n):
    """A three-chunk chapter has no meaningful median.

    Guessing one would re-roll good audio, so the pass declines instead.
    """
    rates = _chunks(0.070, n)
    if rates:
        rates[0] = (0, 200, 0.030 * 200)  # blatantly short, still ignored

    median, suspects = gca._median_sweep_candidates(rates)

    assert (median, suspects) == (0.0, [])


def test_enough_chunks_starts_judging():
    rates = _chunks(0.070, gca.MEDIAN_SWEEP_MIN_CHUNKS)
    rates[0] = (0, 200, 0.030 * 200)

    _, suspects = gca._median_sweep_candidates(rates)

    assert [chi for chi, _, _ in suspects] == [0]


def test_zero_length_audio_does_not_produce_a_zero_median():
    """All-silent input must not make every chunk look healthy."""
    median, suspects = gca._median_sweep_candidates(
        [(i, 200, 0.0) for i in range(12)]
    )
    assert (median, suspects) == (0.0, [])


# ---------------------------------------------------------------------------
# The runaway cap
# ---------------------------------------------------------------------------

def test_many_bad_chunks_are_capped_rather_than_fully_rerolled():
    """If a lot of a chapter is short, re-rolling all of it is not the answer.

    The cause is the text or the voice, and re-rolling burns hours
    changing nothing. Fix the worst, ship the rest, say so.
    """
    rates = _chunks(0.070, 20)
    for i in range(6):
        rates[i] = (i, 200, 0.040 * 200)

    _, suspects = gca._median_sweep_candidates(rates)

    cap = max(1, int(20 * gca.MEDIAN_SWEEP_MAX_FRACTION))
    assert len(suspects) == cap == 5


def test_the_cap_keeps_the_worst_ones():
    rates = _chunks(0.070, 20)
    for i, rate in enumerate([0.030, 0.035, 0.040, 0.045, 0.050, 0.055]):
        rates[i] = (i, 200, rate * 200)

    _, suspects = gca._median_sweep_candidates(rates)

    assert [chi for chi, _, _ in suspects] == [0, 1, 2, 3, 4]


def test_capped_sweep_says_so(capsys):
    """Silent truncation of the work list would read as 'all clear'."""
    rates = _chunks(0.070, 20)
    for i in range(6):
        rates[i] = (i, 200, 0.040 * 200)

    gca._median_sweep_candidates(rates)

    out = capsys.readouterr().out
    assert "too many to be bad rolls" in out


def test_a_majority_broken_chapter_is_invisible_to_this_pass():
    """A known and deliberate blind spot, pinned so it cannot surprise anyone.

    Past half the chunks, the median moves to sit among the broken ones
    and the healthy minority becomes the outlier. Nothing is flagged.

    That is survivable only because the ABSOLUTE guard is still running:
    a collapse that severe puts the chunks under MIN_AUDIO_S_PER_CHAR,
    where the per-chunk band guard retries them as they are generated.
    The two guards cover each other's blind spots, which is the reason
    the absolute one was kept rather than replaced.
    """
    rates = _chunks(0.070, 20)
    for i in range(12):
        rates[i] = (i, 200, 0.040 * 200)

    median, suspects = gca._median_sweep_candidates(rates)

    assert median == pytest.approx(0.040, abs=1e-6)
    assert suspects == []
    # ...and this is what still catches it.
    assert gca._ratio_badness(0.040 * 200, 200) > 0.0


# ---------------------------------------------------------------------------
# Relationship to the absolute guard
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The wiring, with a fake engine — no torch, no CUDA
# ---------------------------------------------------------------------------

class _FakeEngine:
    """Returns audio of a length we dictate, one entry per generate() call."""

    sr = 24000

    def __init__(self, secs_sequence):
        self._secs = list(secs_sequence)
        self.calls = []

    def generate(self, text, **kwargs):
        self.calls.append(text)
        secs = self._secs.pop(0) if self._secs else 0.0
        return _FakeWav(int(secs * self.sr))


class _FakeWav:
    def __init__(self, n):
        self.shape = (1, n)


@pytest.fixture
def sweep_env(tmp_path, monkeypatch):
    """A chapter of 12 chunks on disk, one of them short."""
    chunks_dir = tmp_path / ".chunks"
    chunks_dir.mkdir()
    texts = ["x" * 200] * 12
    # Content-addressed cache: identical text still gets one entry per
    # occurrence, so twelve identical chunks are twelve distinct files.
    keys = gca._chunk_keys(texts, "fi", "")
    by_key = {k: 14.0 for k in keys}           # 0.070 s/char
    by_key[keys[5]] = 12.0                     # 0.060 — short, floor-clearing

    for key in keys:
        gca._chunk_cache_path(chunks_dir, key).write_bytes(b"stub")

    monkeypatch.setattr(
        gca, "_cached_audio_seconds",
        lambda p: by_key[Path(p).stem[len("chunk_"):]],
    )
    saved = {}
    monkeypatch.setitem(
        sys.modules, "torchaudio",
        type(sys)("torchaudio"),
    )
    sys.modules["torchaudio"].save = (
        lambda path, wav, sr: saved.__setitem__(str(path), wav.shape[1] / sr)
    )
    monkeypatch.setattr(gca, "_clear_chatterbox_state", lambda e: None)
    return chunks_dir, texts, saved, keys


def test_sweep_replaces_only_the_short_chunk(sweep_env):
    chunks_dir, texts, saved, keys = sweep_env
    engine = _FakeEngine([14.0])          # the re-roll comes back healthy

    replaced = gca._run_median_sweep(
        engine, chunks_dir, 1, texts, keys, "fi", None
    )

    assert replaced == 1
    assert len(engine.calls) == 1
    assert list(saved.values()) == [pytest.approx(14.0, abs=0.01)]


def test_sweep_keeps_the_original_when_no_reroll_is_better(sweep_env):
    """A worse take must never overwrite a merely-mediocre one."""
    chunks_dir, texts, saved, keys = sweep_env
    engine = _FakeEngine([5.0, 6.0, 4.0])   # every re-roll is worse

    replaced = gca._run_median_sweep(
        engine, chunks_dir, 1, texts, keys, "fi", None
    )

    assert replaced == 0
    assert saved == {}


def test_sweep_rejects_a_rambling_reroll(sweep_env):
    """Longest-is-best would pick the babbler; it must be refused.

    A re-roll far over the rambling edge is certainly not truncated,
    which is exactly why a naive "prefer more audio" rule would ship it.
    """
    chunks_dir, texts, saved, keys = sweep_env
    rambling = gca.MAX_AUDIO_S_PER_CHAR * 200 * 2
    engine = _FakeEngine([rambling, rambling, rambling])

    replaced = gca._run_median_sweep(
        engine, chunks_dir, 1, texts, keys, "fi", None
    )

    assert replaced == 0
    assert saved == {}


def test_sweep_honours_a_stop_request(sweep_env):
    chunks_dir, texts, _, keys = sweep_env
    engine = _FakeEngine([14.0])

    replaced = gca._run_median_sweep(
        engine, chunks_dir, 1, texts, keys, "fi", None,
        should_stop=lambda: True,
    )

    assert replaced == 0
    assert engine.calls == []


def test_sweep_does_nothing_on_a_healthy_chapter(tmp_path, monkeypatch):
    chunks_dir = tmp_path / ".chunks"
    chunks_dir.mkdir()
    texts = ["x" * 200] * 12
    keys = gca._chunk_keys(texts, "fi", "")
    for key in keys:
        gca._chunk_cache_path(chunks_dir, key).write_bytes(b"stub")
    monkeypatch.setattr(gca, "_cached_audio_seconds", lambda p: 14.0)
    engine = _FakeEngine([])

    replaced = gca._run_median_sweep(
        engine, chunks_dir, 1, texts, keys, "fi", None
    )

    assert replaced == 0
    assert engine.calls == []


def test_the_two_guards_are_not_redundant():
    """Documents why both exist.

    The absolute guard fires on gross failures the moment they happen;
    the relative one catches what a constant cannot see. A chapter can
    contain a chunk that only one of them flags.
    """
    rates = _chunks(0.070, 15)
    rates[4] = (4, 200, 0.060 * 200)

    _, suspects = gca._median_sweep_candidates(rates)

    only_relative = gca._ratio_badness(0.060 * 200, 200) == 0.0
    assert only_relative, "the absolute guard is satisfied by this chunk"
    assert [chi for chi, _, _ in suspects] == [4]
