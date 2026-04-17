"""Tests for :mod:`src.voice_pack.align`."""

from __future__ import annotations

from src.voice_pack.align import best_match, realign, split_sentences
from src.voice_pack.types import AsrSegment


# ---------------------------------------------------------------------------
# split_sentences
# ---------------------------------------------------------------------------


def test_split_sentences_basic() -> None:
    assert split_sentences("Hello world. How are you? Fine!") == [
        "Hello world.",
        "How are you?",
        "Fine!",
    ]


def test_split_sentences_collapses_whitespace() -> None:
    assert split_sentences("Hello\n\n  world.  OK!") == ["Hello world.", "OK!"]


def test_split_sentences_empty_and_whitespace() -> None:
    assert split_sentences("") == []
    assert split_sentences("   \n\n  ") == []


def test_split_sentences_no_terminator() -> None:
    assert split_sentences("just some text") == ["just some text"]


# ---------------------------------------------------------------------------
# best_match
# ---------------------------------------------------------------------------


def test_best_match_exact() -> None:
    idx, ratio = best_match("Hello world.", ["nope.", "Hello world.", "other."])
    assert idx == 1
    assert ratio == 1.0


def test_best_match_fuzzy() -> None:
    candidates = [
        "The quick brown fox jumps over the lazy dog.",
        "Something completely different about cats.",
        "A third unrelated sentence.",
    ]
    # Mildly corrupted ASR of the first candidate (typos + a word swap)
    # so the ratio lands comfortably inside the fuzzy band.
    idx, ratio = best_match("the quik brown foks jumps ovr a lzy dog", candidates)
    assert idx == 0
    assert 0.7 <= ratio <= 0.95


def test_best_match_empty() -> None:
    assert best_match("", ["a", "b"]) == (-1, 0.0)
    assert best_match("hello", []) == (-1, 0.0)
    assert best_match("", []) == (-1, 0.0)


# ---------------------------------------------------------------------------
# realign
# ---------------------------------------------------------------------------


def _seg(start: float, end: float, text: str, conf: float = 0.9) -> AsrSegment:
    return AsrSegment(start=start, end=end, text=text, confidence=conf)


def test_realign_replaces_high_similarity_text() -> None:
    segments = [_seg(0.0, 1.0, "helo wrld")]
    reference = "Hello world. Another sentence entirely."
    out = realign(segments, reference, min_similarity=0.6)
    assert len(out) == 1
    assert out[0].text == "Hello world."


def test_realign_leaves_low_similarity_untouched() -> None:
    segments = [_seg(0.0, 1.0, "zzzz qqqq xxxx")]
    reference = "Hello world. How are you today?"
    out = realign(segments, reference, min_similarity=0.6)
    assert out[0].text == "zzzz qqqq xxxx"


def test_realign_preserves_timing_and_confidence() -> None:
    segments = [
        _seg(0.0, 1.25, "helo wrld", conf=0.42),
        _seg(1.25, 2.50, "garbage nothing matches", conf=0.88),
        _seg(2.50, 4.00, "how ar u", conf=0.71),
    ]
    reference = "Hello world. How are you?"
    out = realign(segments, reference, min_similarity=0.5)
    for src, dst in zip(segments, out):
        assert dst.start == src.start
        assert dst.end == src.end
        assert dst.confidence == src.confidence


def test_realign_preserves_order() -> None:
    segments = [
        _seg(0.0, 1.0, "alpha one"),
        _seg(1.0, 2.0, "beta two"),
        _seg(2.0, 3.0, "gamma three"),
    ]
    reference = "Alpha one. Beta two. Gamma three."
    out = realign(segments, reference)
    assert len(out) == len(segments)
    assert [o.text for o in out] == ["Alpha one.", "Beta two.", "Gamma three."]


def test_realign_empty_reference_passthrough() -> None:
    segments = [_seg(0.0, 1.0, "helo wrld")]
    assert realign(segments, "") == segments
    assert realign(segments, "   \n  ") == segments


def test_realign_respects_min_similarity() -> None:
    # "helo wrld stuff" vs "Hello world." has a ratio ~0.667 — inside
    # the (0.6, 0.7) band we want to exercise the boundary.
    reference = "Hello world."
    asr_text = "helo wrld stuff"

    # Probe the actual similarity so the test stays deterministic if
    # SequenceMatcher's implementation is ever tweaked.
    _, probe = best_match(asr_text, [reference])
    assert 0.6 <= probe < 0.7, f"Test construction invalid — got {probe}"

    segments = [_seg(0.0, 1.0, asr_text)]
    out_low = realign(segments, reference, min_similarity=0.6, search_window=0)
    out_high = realign(segments, reference, min_similarity=0.7, search_window=0)
    assert out_low[0].text == "Hello world."
    assert out_high[0].text == asr_text


def test_realign_window_constrains_search() -> None:
    # Reference: a repeated "landmark" sentence appears early AND late.
    # The late occurrence is the true match for our second segment.
    # With search_window=2 and last_match_idx anchored at the late
    # region by the first segment, the algorithm should stay anchored
    # there for the second segment rather than jumping back to the
    # early false-positive.
    reference_sentences = [
        "The landmark sentence was spoken.",  # 0 — early false-positive copy
        "Filler one.",
        "Filler two.",
        "Filler three.",
        "Filler four.",
        "Filler five.",
        "Filler six.",
        "Filler seven.",
        "Filler eight.",
        "Filler nine.",
        "Right before the landmark we had a line.",  # 10
        "The landmark sentence was spoken.",  # 11 — true match
        "After the landmark came this.",  # 12
    ]
    reference = " ".join(reference_sentences)

    # First segment matches sentence 10 exactly — anchors us late.
    # Second segment is a fuzzy version of the landmark sentence.
    segments = [
        _seg(0.0, 1.0, "Right before the landmark we had a line."),
        _seg(1.0, 2.0, "the landmark sentence was spokn"),
    ]

    out = realign(segments, reference, min_similarity=0.6, search_window=2)

    # First segment pins us at index 10.
    assert out[0].text == "Right before the landmark we had a line."
    # Second segment should prefer the in-window copy at index 11,
    # NOT the identical copy at index 0 which is outside the window.
    # We verify via the NEXT segment's anchor: if the algorithm had
    # jumped back to index 0, a subsequent search would search the
    # early window, not the late one. Here we settle for observing
    # that the replacement text is the canonical landmark sentence
    # (both copies have identical text, so we instead assert the
    # third segment — added below — resolves correctly given a late
    # anchor).
    assert out[1].text == "The landmark sentence was spoken."

    # Extend: add a third segment that ONLY exists past the landmark.
    segments_ext = segments + [_seg(2.0, 3.0, "after the landmrk came ths")]
    out_ext = realign(segments_ext, reference, min_similarity=0.6, search_window=2)
    # If the window advanced correctly (staying late after segment 1),
    # segment 2's window covers index 12 and we find "After the landmark…".
    assert out_ext[2].text == "After the landmark came this."


def test_realign_window_falls_back_to_full_scan() -> None:
    # No anchor yet for the very first segment — implementation must
    # either full-scan or start from index 0. Either way, a match that
    # sits well past `search_window` must still be findable.
    filler = ["Filler sentence number {}.".format(i) for i in range(200)]
    target = "The unique needle in a haystack."
    reference_sentences = filler + [target] + filler
    reference = " ".join(reference_sentences)

    segments = [_seg(0.0, 1.0, "the uniqe needle in a haystak")]
    # search_window=5 is far smaller than the index of the target (200).
    out = realign(segments, reference, min_similarity=0.6, search_window=5)
    assert out[0].text == target
