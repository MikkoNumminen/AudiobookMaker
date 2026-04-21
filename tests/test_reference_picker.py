"""Tests for :mod:`src.voice_pack.reference_picker`.

Hermetic. No pydub, no soundfile, no real WAV files. The picker is
designed to accept injected audio I/O callables, so we drive it with
synthetic chunk metadata and recording fakes for read/write.

Per the copyright rule in CLAUDE.md, every fixture is synthetic: lists
of float samples generated in-line, no third-party audio touches this
test file.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from src.voice_pack.reference_picker import (
    DEFAULT_MAX_SECONDS,
    DEFAULT_MIN_SECONDS,
    ReferenceClipReport,
    _derive_fallback_reason,
    _duration_penalty,
    _position_penalty,
    _rms_std,
    _text_penalties,
    load_transcripts,
    pick_reference_clip,
    score_candidate,
)
from src.voice_pack.types import VoiceChunk


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_transcripts(tmp_path: Path, chunks: list[VoiceChunk]) -> Path:
    p = tmp_path / "transcripts.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
    return p


def _chunk(
    start: float,
    end: float,
    speaker: str = "SPEAKER_00",
    text: str = "This is a perfectly ordinary sentence in a clean clip.",
) -> VoiceChunk:
    return VoiceChunk(
        start=start, end=end, text=text, speaker=speaker, confidence=0.9
    )


def _recording_writer() -> tuple[list[tuple[Path, float, float, Path]], callable]:
    """Return a (calls_list, writer) pair for test assertions."""
    calls: list[tuple[Path, float, float, Path]] = []

    def _writer(src: Path, start_s: float, end_s: float, out_path: Path) -> None:
        calls.append((src, start_s, end_s, out_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake")

    return calls, _writer


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


class TestTextPenalties:
    def test_clean_text_has_no_penalty(self) -> None:
        assert _text_penalties("A perfectly ordinary sentence of reasonable length.") == ()

    def test_digits_flagged(self) -> None:
        assert "digits" in _text_penalties("He said 42 things clearly.")

    def test_acronym_flagged(self) -> None:
        assert "acronym" in _text_penalties("She works at NASA on long projects.")

    def test_two_uppercase_letters_not_acronym(self) -> None:
        # "Hi" / "We" etc — two caps in a row would still be rare but
        # two caps at a word boundary isn't an acronym by our rule.
        assert "acronym" not in _text_penalties("Hi there we are friends indeed.")

    def test_too_few_words(self) -> None:
        assert "too_few_words" in _text_penalties("Too short here.")

    def test_too_many_words(self) -> None:
        long_text = " ".join(["word"] * 90)
        assert "too_many_words" in _text_penalties(long_text)

    def test_finnish_acronym_flagged(self) -> None:
        # Uppercase ÄÖÅ should count for acronym detection.
        assert "acronym" in _text_penalties("KÄÖ on outo lyhenne tekstissä täällä.")


class TestDurationPenalty:
    def test_inside_window_is_zero(self) -> None:
        p, tags = _duration_penalty(15.0, 12.0, 18.0)
        assert p == 0.0
        assert tags == ()

    def test_under_window(self) -> None:
        p, tags = _duration_penalty(9.0, 12.0, 18.0)
        assert p == pytest.approx(3.0)
        assert tags == ("too_short",)

    def test_over_window(self) -> None:
        p, tags = _duration_penalty(22.0, 12.0, 18.0)
        assert p == pytest.approx(4.0)
        assert tags == ("too_long",)


class TestPositionPenalty:
    def test_middle_of_source(self) -> None:
        p, tags = _position_penalty(100.0, 115.0, 600.0, 5.0)
        assert p == 0.0
        assert tags == ()

    def test_intro_region(self) -> None:
        p, tags = _position_penalty(1.0, 16.0, 600.0, 5.0)
        assert "intro" in tags
        assert p > 0.0

    def test_outro_region(self) -> None:
        p, tags = _position_penalty(580.0, 598.0, 600.0, 5.0)
        assert "outro" in tags
        assert p > 0.0


class TestRmsStd:
    def test_empty_returns_zero(self) -> None:
        assert _rms_std([], 24000) == 0.0

    def test_constant_amplitude_has_low_std(self) -> None:
        # Steady sine-like signal at constant amplitude ⇒ stable RMS.
        sr = 24000
        samples = [0.3 * math.sin(2 * math.pi * 200.0 * i / sr) for i in range(sr)]
        assert _rms_std(samples, sr) < 0.05

    def test_swelling_amplitude_has_high_std(self) -> None:
        # Amplitude ramp from 0 to 1 ⇒ per-window RMS climbs ⇒ higher std.
        sr = 24000
        samples = [
            (i / sr) * math.sin(2 * math.pi * 200.0 * i / sr) for i in range(sr)
        ]
        constant_samples = [
            0.3 * math.sin(2 * math.pi * 200.0 * i / sr) for i in range(sr)
        ]
        assert _rms_std(samples, sr) > _rms_std(constant_samples, sr)


# ---------------------------------------------------------------------------
# score_candidate
# ---------------------------------------------------------------------------


class TestScoreCandidate:
    def test_perfect_chunk_scores_zero(self) -> None:
        c = _chunk(100.0, 115.0)
        score, tags = score_candidate(
            c, source_duration=600.0, min_seconds=12.0, max_seconds=18.0
        )
        assert score == 0.0
        assert tags == ()

    def test_digits_penalty_dominates_small_duration_miss(self) -> None:
        good = _chunk(100.0, 110.0)  # 10 s — 2 s under window, penalty 2.0
        digits = _chunk(
            120.0, 135.0, text="We had 42 problems when they rang, we did."
        )
        sg, _ = score_candidate(
            good, 600.0, min_seconds=12.0, max_seconds=18.0
        )
        sd, _ = score_candidate(
            digits, 600.0, min_seconds=12.0, max_seconds=18.0
        )
        # Duration miss (penalty 2.0) < one text-penalty (weight 3.0)
        assert sg < sd

    def test_rms_std_term_added_in(self) -> None:
        c = _chunk(100.0, 115.0)
        without, _ = score_candidate(
            c, 600.0, min_seconds=12.0, max_seconds=18.0, rms_std=0.0
        )
        with_, _ = score_candidate(
            c, 600.0, min_seconds=12.0, max_seconds=18.0, rms_std=0.5
        )
        assert with_ > without


# ---------------------------------------------------------------------------
# load_transcripts
# ---------------------------------------------------------------------------


class TestLoadTranscripts:
    def test_round_trip(self, tmp_path: Path) -> None:
        chunks = [
            _chunk(0.0, 2.0, text="Short line here right now today friend."),
            _chunk(2.0, 17.0, speaker="SPEAKER_01"),
        ]
        p = _write_transcripts(tmp_path, chunks)
        out = load_transcripts(p)
        assert len(out) == 2
        assert out[0].speaker == "SPEAKER_00"
        assert out[1].speaker == "SPEAKER_01"

    def test_malformed_row_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "transcripts.jsonl"
        p.write_text("not json at all\n", encoding="utf-8")
        with pytest.raises(ValueError, match="could not parse"):
            load_transcripts(p)


# ---------------------------------------------------------------------------
# pick_reference_clip — end-to-end with injected I/O
# ---------------------------------------------------------------------------


class TestPickReferenceClip:
    def test_picks_clean_in_window_chunk_over_bad_chunks(
        self, tmp_path: Path
    ) -> None:
        # Three speaker-00 chunks. The middle one is a clean 15s clip.
        chunks = [
            # Intro overlap — starts at 1.0, would get intro penalty.
            _chunk(1.0, 16.0),
            # Winner — middle of source, 15 s, clean text.
            _chunk(120.0, 135.0),
            # Digit-laden text.
            _chunk(
                200.0,
                215.0,
                text="Chapter 42: we spoke about 3 things that day, friend.",
            ),
            # Other speaker — must be ignored.
            _chunk(
                300.0, 315.0, speaker="SPEAKER_01", text="Other speaker saying nice things."
            ),
        ]
        p = _write_transcripts(tmp_path, chunks)
        calls, writer = _recording_writer()
        out = tmp_path / "picked" / "reference.wav"

        report = pick_reference_clip(
            transcripts=p,
            speaker_id="SPEAKER_00",
            wav_source=tmp_path / "fake_source.wav",
            out_path=out,
            audio_reader=None,  # metadata-only scoring
            audio_writer=writer,
            source_duration=600.0,
        )

        assert isinstance(report, ReferenceClipReport)
        assert report.selected_start == 120.0
        assert report.selected_end == 135.0
        assert report.selected_duration == pytest.approx(15.0)
        assert report.candidate_count == 3  # speaker-00 chunks only
        assert report.fallback_reason is None
        assert len(calls) == 1
        assert calls[0][1] == 120.0
        assert calls[0][2] == 135.0
        assert calls[0][3] == out
        assert out.exists()

    def test_excludes_other_speakers(self, tmp_path: Path) -> None:
        chunks = [
            _chunk(
                100.0, 115.0, speaker="SPEAKER_01", text="Nope this is a different speaker."
            ),
            _chunk(200.0, 215.0, speaker="SPEAKER_00"),
        ]
        p = _write_transcripts(tmp_path, chunks)
        _, writer = _recording_writer()

        report = pick_reference_clip(
            transcripts=p,
            speaker_id="SPEAKER_00",
            wav_source=tmp_path / "fake.wav",
            out_path=tmp_path / "ref.wav",
            audio_writer=writer,
            source_duration=300.0,
        )
        assert report.selected_start == 200.0

    def test_raises_when_no_chunks_for_speaker(self, tmp_path: Path) -> None:
        chunks = [_chunk(100.0, 115.0, speaker="SPEAKER_00")]
        p = _write_transcripts(tmp_path, chunks)
        _, writer = _recording_writer()
        with pytest.raises(ValueError, match="no chunks for speaker"):
            pick_reference_clip(
                transcripts=p,
                speaker_id="SPEAKER_09",
                wav_source=tmp_path / "fake.wav",
                out_path=tmp_path / "ref.wav",
                audio_writer=writer,
            )

    def test_fallback_reason_when_nothing_in_window(
        self, tmp_path: Path
    ) -> None:
        # Only short chunks exist — picker still returns one, with a
        # fallback note.
        chunks = [_chunk(100.0, 105.0) for _ in range(3)]
        p = _write_transcripts(tmp_path, chunks)
        _, writer = _recording_writer()

        report = pick_reference_clip(
            transcripts=p,
            speaker_id="SPEAKER_00",
            wav_source=tmp_path / "fake.wav",
            out_path=tmp_path / "ref.wav",
            audio_writer=writer,
            source_duration=600.0,
        )
        assert report.fallback_reason is not None
        assert "12-18s window" in report.fallback_reason or "12–18" in report.fallback_reason or "window" in report.fallback_reason

    def test_audio_reader_only_called_on_top_k(self, tmp_path: Path) -> None:
        # 10 speaker chunks — reader must only be invoked top_k times.
        chunks = [_chunk(20.0 * i + 20.0, 20.0 * i + 35.0) for i in range(10)]
        p = _write_transcripts(tmp_path, chunks)
        _, writer = _recording_writer()

        reads: list[tuple[Path, float, float]] = []

        def _reader(src: Path, start_s: float, end_s: float) -> list[float]:
            reads.append((src, start_s, end_s))
            return [0.1, 0.1, 0.1, 0.1]

        pick_reference_clip(
            transcripts=p,
            speaker_id="SPEAKER_00",
            wav_source=tmp_path / "fake.wav",
            out_path=tmp_path / "ref.wav",
            audio_reader=_reader,
            audio_writer=writer,
            source_duration=600.0,
            top_k=3,
        )
        assert len(reads) == 3

    def test_rms_breaks_ties_between_otherwise_equal_candidates(
        self, tmp_path: Path
    ) -> None:
        # Two candidates with identical metadata scores. RMS reader
        # reports the second as more stable ⇒ second should win.
        chunks = [
            _chunk(100.0, 115.0),
            _chunk(200.0, 215.0),
            _chunk(300.0, 315.0),
        ]
        p = _write_transcripts(tmp_path, chunks)
        calls, writer = _recording_writer()

        # 15s worth of samples at 24kHz → 75 200ms windows, plenty to
        # compute a real per-window RMS std.
        _n_samples = 24000 * 15

        def _reader(src: Path, start_s: float, end_s: float) -> list[float]:
            # Chunk at 200.0 is the "stable" one — low rms std.
            if abs(start_s - 200.0) < 0.01:
                return [0.3] * _n_samples  # constant amplitude ⇒ rms std 0
            # Others are swelling amplitude ⇒ rms std > 0.
            return [(i / _n_samples) for i in range(_n_samples)]

        report = pick_reference_clip(
            transcripts=p,
            speaker_id="SPEAKER_00",
            wav_source=tmp_path / "fake.wav",
            out_path=tmp_path / "ref.wav",
            audio_reader=_reader,
            audio_writer=writer,
            source_duration=600.0,
        )
        assert report.selected_start == 200.0

    def test_source_duration_inferred_from_last_chunk(
        self, tmp_path: Path
    ) -> None:
        # Only one 15s chunk, ending exactly at the inferred source
        # end — outro penalty should fire.
        chunks = [_chunk(30.0, 45.0)]
        p = _write_transcripts(tmp_path, chunks)
        _, writer = _recording_writer()

        report = pick_reference_clip(
            transcripts=p,
            speaker_id="SPEAKER_00",
            wav_source=tmp_path / "fake.wav",
            out_path=tmp_path / "ref.wav",
            audio_writer=writer,
        )
        assert report.fallback_reason is not None
        assert "start or end" in report.fallback_reason


# ---------------------------------------------------------------------------
# _derive_fallback_reason
# ---------------------------------------------------------------------------


class TestDeriveFallbackReason:
    def test_clean_candidate_returns_none(self) -> None:
        from src.voice_pack.reference_picker import ReferenceClipCandidate

        c = ReferenceClipCandidate(
            chunk_index=0,
            start=100.0,
            end=115.0,
            duration=15.0,
            score=0.0,
            rms_std=0.0,
            text_preview="clean",
            penalties=(),
        )
        assert (
            _derive_fallback_reason(
                c, min_seconds=DEFAULT_MIN_SECONDS, max_seconds=DEFAULT_MAX_SECONDS
            )
            is None
        )

    def test_duration_penalty_surfaced(self) -> None:
        from src.voice_pack.reference_picker import ReferenceClipCandidate

        c = ReferenceClipCandidate(
            chunk_index=0,
            start=100.0,
            end=105.0,
            duration=5.0,
            score=7.0,
            rms_std=0.0,
            text_preview="short",
            penalties=("too_short",),
        )
        reason = _derive_fallback_reason(c, min_seconds=12.0, max_seconds=18.0)
        assert reason is not None
        assert "5.0s" in reason or "5s" in reason
