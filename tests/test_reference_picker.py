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
    _estimate_median_f0_hz,
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
# _estimate_median_f0_hz
# ---------------------------------------------------------------------------

# F0 estimator needs numpy. Skip cleanly in environments where numpy
# isn't installed — the picker itself returns None in that case, so the
# function is still callable from those environments (it just always
# returns None).
np = pytest.importorskip("numpy")


def _sine(sample_rate: int, freq_hz: float, seconds: float, amp: float = 0.3) -> list[float]:
    """Return a mono sine-wave sample list at the given frequency."""
    n = int(seconds * sample_rate)
    return [amp * math.sin(2 * math.pi * freq_hz * i / sample_rate) for i in range(n)]


class TestEstimateMedianF0:
    def test_empty_returns_none(self) -> None:
        assert _estimate_median_f0_hz([], 24000) is None

    def test_too_short_returns_none(self) -> None:
        # 0.1 s at 24 kHz = 2400 samples — below the 0.5 s minimum.
        sr = 24000
        samples = _sine(sr, 200.0, 0.1)
        assert _estimate_median_f0_hz(samples, sr) is None

    def test_silent_clip_returns_none(self) -> None:
        # All zero → overall RMS below the 1e-6 floor → None.
        sr = 24000
        assert _estimate_median_f0_hz([0.0] * sr, sr) is None

    def test_recovers_known_female_pitch(self) -> None:
        # 200 Hz is comfortably inside adult female F0 range (170–250 Hz).
        sr = 24000
        samples = _sine(sr, 200.0, 1.5)
        f0 = _estimate_median_f0_hz(samples, sr)
        assert f0 is not None
        # Autocorrelation lag is integer-quantised, so allow ±5 Hz at
        # 200 Hz (24000/121=198.3, 24000/120=200.0, 24000/119=201.7).
        assert abs(f0 - 200.0) < 5.0

    def test_recovers_known_male_pitch(self) -> None:
        # 110 Hz is comfortably inside adult male F0 range (85–155 Hz).
        sr = 24000
        samples = _sine(sr, 110.0, 1.5)
        f0 = _estimate_median_f0_hz(samples, sr)
        assert f0 is not None
        assert abs(f0 - 110.0) < 5.0

    def test_distinguishes_pitches_clearly(self) -> None:
        sr = 24000
        female = _estimate_median_f0_hz(_sine(sr, 220.0, 1.5), sr)
        male = _estimate_median_f0_hz(_sine(sr, 100.0, 1.5), sr)
        assert female is not None and male is not None
        # The gap should be much larger than the autocorrelation
        # quantisation error.
        assert female - male > 80.0


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

    def test_pitch_deviation_adds_penalty(self) -> None:
        c = _chunk(100.0, 115.0)
        baseline_score, _ = score_candidate(
            c, 600.0, min_seconds=12.0, max_seconds=18.0, pitch_deviation_hz=0.0
        )
        deviated_score, tags = score_candidate(
            c, 600.0, min_seconds=12.0, max_seconds=18.0, pitch_deviation_hz=25.0
        )
        # 25 Hz deviation × _W_PITCH (0.02) = 0.5 added.
        assert deviated_score > baseline_score
        assert deviated_score - baseline_score == pytest.approx(0.5)
        # Below the 80 Hz outlier threshold, no tag should fire.
        assert "pitch_outlier" not in tags

    def test_large_pitch_deviation_flagged_as_outlier(self) -> None:
        c = _chunk(100.0, 115.0)
        _, tags = score_candidate(
            c, 600.0, min_seconds=12.0, max_seconds=18.0, pitch_deviation_hz=120.0
        )
        # 120 Hz is roughly the gap between adult male and adult female
        # F0 medians — treat as a hard outlier worth flagging.
        assert "pitch_outlier" in tags

    def test_pitch_default_is_zero(self) -> None:
        c = _chunk(100.0, 115.0)
        # Old call sites that don't pass pitch_deviation_hz must continue
        # to score identically to before.
        new_default, _ = score_candidate(
            c, 600.0, min_seconds=12.0, max_seconds=18.0, rms_std=0.3
        )
        explicit_zero, _ = score_candidate(
            c, 600.0, min_seconds=12.0, max_seconds=18.0,
            rms_std=0.3, pitch_deviation_hz=0.0,
        )
        assert new_default == explicit_zero


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
        # Kept for backwards-compat coverage; now delegates to the
        # unknown-speaker branch so the message changed.
        chunks = [_chunk(100.0, 115.0, speaker="SPEAKER_00")]
        p = _write_transcripts(tmp_path, chunks)
        _, writer = _recording_writer()
        with pytest.raises(ValueError, match="not found"):
            pick_reference_clip(
                transcripts=p,
                speaker_id="SPEAKER_09",
                wav_source=tmp_path / "fake.wav",
                out_path=tmp_path / "ref.wav",
                audio_writer=writer,
            )

    def test_pick_reference_clip_unknown_speaker_id(self, tmp_path: Path) -> None:
        # Transcripts contain SPEAKER_00 and SPEAKER_01.  Asking for
        # SPEAKER_99 must raise ValueError with a message that names the
        # missing speaker and lists the ones that *are* present so a
        # debugger can immediately see the discrepancy.
        chunks = [
            _chunk(100.0, 115.0, speaker="SPEAKER_00"),
            _chunk(200.0, 215.0, speaker="SPEAKER_01"),
        ]
        p = _write_transcripts(tmp_path, chunks)
        _, writer = _recording_writer()

        with pytest.raises(ValueError) as exc_info:
            pick_reference_clip(
                transcripts=p,
                speaker_id="SPEAKER_99",
                wav_source=tmp_path / "fake.wav",
                out_path=tmp_path / "ref.wav",
                audio_writer=writer,
            )

        msg = str(exc_info.value)
        assert "not found" in msg, f"expected 'not found' in message: {msg!r}"
        assert "SPEAKER_00" in msg, f"expected available speaker in message: {msg!r}"
        assert "SPEAKER_01" in msg, f"expected available speaker in message: {msg!r}"

    def test_pick_reference_clip_speaker_present_but_no_chunks(
        self, tmp_path: Path
    ) -> None:
        # NOTE: In the current implementation the speaker_chunks list
        # comprehension at pick_reference_clip() only filters by
        # c.speaker == speaker_id.  There is no upstream quality or
        # duration filter inside pick_reference_clip() itself — those
        # filters run as soft scoring, not hard exclusions.  Therefore
        # the "speaker present but all chunks filtered" branch
        # (the second ValueError inside the `if not speaker_chunks` block)
        # cannot be reached through pick_reference_clip() today: any
        # speaker that appears in the transcripts will always have at
        # least one chunk pass the speaker-match step.
        #
        # The branch exists to guard future refactors that might add hard
        # exclusion filters (e.g. min-confidence gating) upstream of the
        # list comprehension.  If such a filter is added, add a test here
        # that constructs a transcripts file where SPEAKER_01 chunks fail
        # the new filter and assert ValueError with "no usable chunks" in
        # the message.
        pass

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

    def test_pitch_outlier_candidate_demoted(self, tmp_path: Path) -> None:
        """Three otherwise-equal candidates; one has an outlier F0.

        Reproduces the 2026-05-10 failure mode: the picker would land on
        a 12 s clip that scored well on every metadata heuristic but
        happened to catch the speaker in a deep / emphatic moment, and
        the resulting voice clone came out gendered wrong. With the
        pitch-deviation term, the outlier should be demoted below the
        two near-baseline candidates.
        """
        chunks = [
            _chunk(100.0, 115.0),  # baseline pitch (220 Hz)
            _chunk(200.0, 215.0),  # baseline pitch (220 Hz)
            _chunk(300.0, 315.0),  # OUTLIER (100 Hz — emphatic / deep)
        ]
        p = _write_transcripts(tmp_path, chunks)
        _, writer = _recording_writer()

        sr = 24000
        female_samples = _sine(sr, 220.0, 15.0)
        male_samples = _sine(sr, 100.0, 15.0)

        def _reader(src: Path, start_s: float, end_s: float) -> list[float]:
            if abs(start_s - 300.0) < 0.01:
                return male_samples  # outlier
            return female_samples

        report = pick_reference_clip(
            transcripts=p,
            speaker_id="SPEAKER_00",
            wav_source=tmp_path / "fake.wav",
            out_path=tmp_path / "ref.wav",
            audio_reader=_reader,
            audio_writer=writer,
            source_duration=600.0,
            top_k=5,
        )

        # The pick should NOT be the 300.0 s outlier.
        assert report.selected_start in (100.0, 200.0)

        # The candidate at 300.0 should appear in the report with both a
        # populated median_f0_hz and a positive deviation cost reflected
        # in its score being highest.
        outlier = next(c for c in report.candidates if abs(c.start - 300.0) < 0.01)
        baseline = next(c for c in report.candidates if abs(c.start - 100.0) < 0.01)
        assert outlier.median_f0_hz is not None
        assert baseline.median_f0_hz is not None
        assert abs(outlier.median_f0_hz - 100.0) < 5.0
        assert abs(baseline.median_f0_hz - 220.0) < 5.0
        assert outlier.score > baseline.score
        # The 120 Hz gap (220 → 100) should trip the outlier tag.
        assert "pitch_outlier" in outlier.penalties

    def test_pitch_term_skipped_when_audio_reader_absent(
        self, tmp_path: Path
    ) -> None:
        """Metadata-only path produces candidates with median_f0_hz=None."""
        chunks = [_chunk(100.0, 115.0), _chunk(200.0, 215.0)]
        p = _write_transcripts(tmp_path, chunks)
        _, writer = _recording_writer()

        report = pick_reference_clip(
            transcripts=p,
            speaker_id="SPEAKER_00",
            wav_source=tmp_path / "fake.wav",
            out_path=tmp_path / "ref.wav",
            audio_reader=None,
            audio_writer=writer,
            source_duration=600.0,
        )
        for c in report.candidates:
            assert c.median_f0_hz is None

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
