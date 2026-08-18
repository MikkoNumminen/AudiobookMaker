"""Tests for splitting an oversized chapter into parts at assembly time.

A book with no detectable structure parses as ONE chapter. A tester's did, at
2229 chunks — roughly eight hours of audio — and the final assembly holds the
whole thing in memory several times over (a PCM buffer, a copy of it, then
pydub's pure-Python low-pass filter on top). That is gigabytes at the very last
step, after fourteen hours of synthesis had already succeeded.

Splitting at ASSEMBLY time rather than in the plan is deliberate: the chunk
cache filenames stay `ch{pos}_chunk{i}`, so a partial run made by an older
build still resumes.

The correctness bar: audio at a part boundary must match what a single-file
assembly would have produced, which means seams are decided by a chunk's
position in the CHAPTER, never in its part.
"""
from __future__ import annotations

import importlib.util
import struct
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def runner():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "generate_chatterbox_audiobook.py"
    )
    spec = importlib.util.spec_from_file_location("_abm_runner_parts", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_abm_runner_parts"] = mod
    spec.loader.exec_module(mod)
    return mod


def _needs_audio_decoder():
    """Skip when the audio toolchain is absent.

    Decoding a 32-bit float WAV goes through ffmpeg/ffprobe: pydub's built-in
    reader does not handle `audio_format=0x0003`, so it shells out. CI runners
    for the pure-Python jobs have neither binary, and a missing decoder is not
    a defect in the code under test.
    """
    import shutil
    if shutil.which("ffprobe") is None and shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg/ffprobe not available; cannot decode float32 WAV")


class TestPartRanges:
    def test_a_short_chapter_is_one_part(self, runner):
        assert runner._chapter_part_ranges(10, per_part=400) == [(0, 10)]

    def test_exactly_the_limit_is_still_one_part(self, runner):
        assert runner._chapter_part_ranges(400, per_part=400) == [(0, 400)]

    def test_one_over_the_limit_splits(self, runner):
        assert runner._chapter_part_ranges(401, per_part=400) == [(0, 400), (400, 401)]

    def test_ranges_cover_every_chunk_exactly_once(self, runner):
        ranges = runner._chapter_part_ranges(2229, per_part=400)
        covered = [i for start, end in ranges for i in range(start, end)]
        assert covered == list(range(2229)), "a chunk was dropped or duplicated"

    def test_empty_chapter_yields_nothing(self, runner):
        assert runner._chapter_part_ranges(0) == []

    def test_the_real_incident_splits_into_six(self, runner):
        assert len(runner._chapter_part_ranges(2229)) == 6


class TestPartNaming:
    def test_single_part_keeps_the_original_name(self, runner, tmp_path):
        p = tmp_path / "01_intro.mp3"
        assert runner._part_path(p, 0, 1) == p

    def test_multiple_parts_are_numbered(self, runner, tmp_path):
        p = tmp_path / "01_intro.mp3"
        assert runner._part_path(p, 0, 3).name == "01_intro_part01.mp3"
        assert runner._part_path(p, 2, 3).name == "01_intro_part03.mp3"

    def test_parts_sort_in_playback_order(self, runner, tmp_path):
        """Zero-padded so a file listing is also the listening order."""
        p = tmp_path / "01_intro.mp3"
        names = [runner._part_path(p, i, 12).name for i in range(12)]
        assert names == sorted(names)


class TestSeamsUseChapterPosition:
    """The correctness bar for splitting: identical audio at a boundary."""

    def _seg(self, ms=200):
        pytest.importorskip("pydub")
        from pydub import AudioSegment
        return AudioSegment.silent(duration=ms, frame_rate=24000).set_sample_width(2)

    def test_split_assembly_matches_whole_assembly(self, runner):
        """Assembling 0-3 then 4-7 must equal assembling 0-7 in one pass."""
        pytest.importorskip("pydub")
        texts = ["Ensimmäinen virke.", "Toinen, pilkulla", "Kolmas!", "Neljäs",
                 "Viides.", "Kuudes?", "Seitsemäs", "Kahdeksas."]

        whole = runner._assemble_chunks(
            (self._seg() for _ in texts), texts,
        )
        first = runner._assemble_chunks(
            (self._seg() for _ in texts[:4]), texts,
            index_offset=0, total=len(texts),
        )
        second = runner._assemble_chunks(
            (self._seg() for _ in texts[4:]), texts,
            index_offset=4, total=len(texts),
        )
        assert first.raw_data + second.raw_data == whole.raw_data

    def test_offset_slice_does_not_reopen_the_chapter(self, runner):
        """Chunk 0 keeps its full leading silence because it opens the
        chapter. Judged per slice, every boundary would gain that pause."""
        pytest.importorskip("pydub")
        texts = ["Yksi.", "Kaksi.", "Kolme.", "Neljä."]
        as_slice = runner._assemble_chunks(
            (self._seg() for _ in texts[2:]), texts,
            index_offset=2, total=len(texts),
        )
        as_own_chapter = runner._assemble_chunks(
            (self._seg() for _ in texts[2:]), texts[2:],
        )
        # The slice is a mid-chapter continuation, so its lead-in is tightened
        # and its content cannot be identical to a standalone chapter's.
        assert len(as_slice.raw_data) <= len(as_own_chapter.raw_data)

    def test_defaults_reproduce_the_old_behaviour(self, runner):
        """Callers that pass neither argument must be unaffected."""
        pytest.importorskip("pydub")
        texts = ["Yksi.", "Kaksi.", "Kolme."]
        a = runner._assemble_chunks((self._seg() for _ in texts), texts)
        b = runner._assemble_chunks(
            (self._seg() for _ in texts), texts,
            index_offset=0, total=len(texts),
        )
        assert a.raw_data == b.raw_data


class TestIterTrimmedChunksRange:
    def _float32_wav(self, path, seconds=0.2, rate=24000):
        n = int(rate * seconds)
        samples = b"".join(struct.pack("<f", 0.25) for _ in range(n))
        hdr = b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVE"
        hdr += b"fmt " + struct.pack("<IHHIIHH", 16, 3, 1, rate, rate * 4, 4, 32)
        hdr += b"data" + struct.pack("<I", len(samples))
        path.write_bytes(hdr + samples)

    def test_start_skips_earlier_chunks(self, runner, tmp_path):
        pytest.importorskip("pydub")
        _needs_audio_decoder()
        for chi in range(5):
            self._float32_wav(tmp_path / f"ch01_chunk{chi:04d}.wav")
        got = list(
            runner._iter_trimmed_chunks(tmp_path, 1, 5, None, None, start=3)
        )
        assert len(got) == 2

    def test_default_start_reads_everything(self, runner, tmp_path):
        pytest.importorskip("pydub")
        _needs_audio_decoder()
        for chi in range(3):
            self._float32_wav(tmp_path / f"ch01_chunk{chi:04d}.wav")
        got = list(runner._iter_trimmed_chunks(tmp_path, 1, 3, None, None))
        assert len(got) == 3


class TestFullBookConcatIsStreamed:
    """Splitting a chapter woke a dormant memory bomb in the full-book concat."""

    def test_concat_does_not_build_one_audiosegment(self, runner):
        """`full += AudioSegment.from_file(p)` over every part is the whole
        book in RAM, then `_postprocess` copies it several more times — the
        exact failure the split was added to prevent, reintroduced at the
        very last step of the run."""
        src = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "generate_chatterbox_audiobook.py"
        ).read_text(encoding="utf-8")
        assert "full += AudioSegment.from_file" not in src
        assert "_concat_to_full" in src

    def test_concat_does_not_postprocess_again(self, runner):
        """Every input was already low-passed and gain-normalized before it was
        written, so doing it again to the concatenation double-applies both."""
        src = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "generate_chatterbox_audiobook.py"
        ).read_text(encoding="utf-8")
        i = src.index("def _concat_to_full")
        # Slice to the NEXT top-level def, or the window runs on into
        # _postprocess's own definition and the assertion is meaningless.
        j = src.index("\ndef ", i + 1)
        # Assert on the CALL, not the name: the docstring explains at length
        # why it is absent, so a bare name check matches its own rationale.
        assert "_postprocess(" not in src[i:j]

    def test_concat_failure_is_reported_not_fatal(self, runner):
        src = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "generate_chatterbox_audiobook.py"
        ).read_text(encoding="utf-8")
        assert "[error] ran out of memory building the full-book MP3" in src


class TestPartBoundariesGetNoChapterGap:
    """A split chapter is one continuous narration stored as several files."""

    def test_gap_list_marks_only_the_last_part(self, runner):
        src = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "generate_chatterbox_audiobook.py"
        ).read_text(encoding="utf-8")
        assert "chapter_gap_after" in src
        assert "[0] * (len(written_parts) - 1) + [INTER_CHAPTER_SILENCE_MS]" in src

    def test_progress_json_lists_every_part(self, runner):
        """Recording only one file leaves any consumer pointing at a fraction
        of the chapter."""
        src = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "generate_chatterbox_audiobook.py"
        ).read_text(encoding="utf-8")
        assert '"parts": [' in src

    def test_progress_json_points_at_the_first_part(self, runner):
        src = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "generate_chatterbox_audiobook.py"
        ).read_text(encoding="utf-8")
        assert "chapter_mp3 = written_parts[0][0]" in src
