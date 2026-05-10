"""Unit tests for :mod:`src.voice_pack_chunked_subproc`.

End-to-end tests with every I/O boundary faked: probe_duration, the
ffmpeg slicer, the per-chunk analyse subprocess, and the embedder.
The test fixtures supply hand-crafted "transcripts.jsonl" files in
the chunk-output directories so the merger has real disk artefacts
to read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pytest

from src.voice_pack.ffmpeg_slice import SliceRequest
from src.voice_pack.reconcile import LocalSpeakerKey
from src.voice_pack.types import VoiceChunk
from src.voice_pack_chunked_subproc import (
    STAGE_CHUNK_DONE,
    STAGE_CHUNK_FAILED,
    STAGE_CHUNK_RETRY,
    STAGE_DONE,
    STAGE_ERROR,
    STAGE_PROBE,
    ChunkAnalyzeRecord,
    ChunkedProgress,
    merge_transcripts,
    render_chunked_report,
    run_chunked_analyze,
    summarize_global_speakers,
)
from src.voice_pack_chunked_subproc import (
    plan_chunks as _plan_chunks_re_export,  # smoke-import stability
)


# ---------------------------------------------------------------------------
# fixture-friendly fakes
# ---------------------------------------------------------------------------


def _fake_probe(duration: float):
    def _probe(_path: Path) -> float:
        return duration
    return _probe


def _fake_slicer(touch: bool = True):
    """Slicer fake that just touches the output file."""
    def _slice(req: SliceRequest, **_kw):
        if touch:
            req.out_path.parent.mkdir(parents=True, exist_ok=True)
            req.out_path.write_bytes(b"RIFF...")
        return req.out_path
    return _slice


class _FakeAnalyzeResult:
    """Minimal AnalyzeJobResult lookalike."""

    def __init__(self, *, ok: bool, out_dir: Path, error: Optional[str] = None):
        self.ok = ok
        self.error = error
        self.transcripts_path = out_dir / "transcripts.jsonl"
        self.speakers_yaml_path = out_dir / "speakers.yaml"
        self.report_path = out_dir / "report.md"
        self.return_code = 0 if ok else 127
        self.log_lines: list[str] = []


def _make_chunk_analyse_fn(
    transcripts_by_chunk_index: dict[int, list[VoiceChunk]],
    *,
    fail_on_first_call: set[int] | None = None,
):
    """Build a chunk-analyse fake that writes fixture transcripts.

    Each chunk index gets its canned set of VoiceChunks. If the index
    is in ``fail_on_first_call``, the first call returns ok=False; the
    second (cpu fallback) succeeds. This exercises the retry path.
    """
    fail_state = {idx: False for idx in (fail_on_first_call or set())}

    def _analyze(**kwargs):
        out_dir = Path(kwargs["out_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        # Recover chunk index from the chunk subdirectory name; the
        # orchestrator passes chunks/chunk_NNN/.
        idx = int(out_dir.name.split("_")[-1])
        # Detect fallback retry — the orchestrator appends --asr-device cpu.
        is_fallback = "cpu" in (kwargs.get("extra_argv") or [])

        if idx in fail_state and not fail_state[idx] and not is_fallback:
            fail_state[idx] = True
            return _FakeAnalyzeResult(
                ok=False, out_dir=out_dir, error="STATUS_STACK_BUFFER_OVERRUN",
            )

        chunks = transcripts_by_chunk_index.get(idx, [])
        # Write transcripts.jsonl in the chunk's local timeline so the
        # orchestrator's merger has real input to translate.
        with (out_dir / "transcripts.jsonl").open("w", encoding="utf-8") as fh:
            for c in chunks:
                fh.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
        # Write a stub speakers.yaml — the orchestrator does its own
        # global summarisation, so contents don't matter here.
        (out_dir / "speakers.yaml").write_text("[]", encoding="utf-8")
        return _FakeAnalyzeResult(ok=True, out_dir=out_dir)

    return _analyze


def _make_embed_fn(speaker_to_vec: dict[tuple[int, str], np.ndarray]):
    """Embedder fake keyed by (chunk_index, local_speaker)."""

    def _embed(chunk_wav: Path, transcripts_path: Path) -> dict[str, object]:
        # Recover chunk index from the parent directory name layout:
        # chunks/chunk_NNN.wav lives next to chunks/chunk_NNN/.
        idx = int(chunk_wav.stem.split("_")[-1])
        out: dict[str, object] = {}
        for (chunk_idx, speaker), vec in speaker_to_vec.items():
            if chunk_idx == idx:
                out[speaker] = vec
        return out

    return _embed


# ---------------------------------------------------------------------------
# merge_transcripts (pure)
# ---------------------------------------------------------------------------


class TestMergeTranscripts:
    def test_translates_timestamps_into_global_timeline(self, tmp_path: Path):
        # Chunk 0 covers [0, 300] (no overlap), chunk 1 covers
        # [300, 600] (no overlap). The chunk-1 transcript at local
        # t=10 must land at global t=310.
        from src.voice_pack.chunked import AudioChunkPlan

        plan_a = AudioChunkPlan(0, 0.0, 300.0, 0.0, 300.0)
        plan_b = AudioChunkPlan(1, 300.0, 600.0, 300.0, 600.0)

        def _loader(path: Path):
            local_chunks = {
                0: [VoiceChunk(start=5.0, end=15.0, text="hi",
                               speaker="SPEAKER_00", confidence=0.9)],
                1: [VoiceChunk(start=10.0, end=20.0, text="yo",
                               speaker="SPEAKER_00", confidence=0.85)],
            }
            return local_chunks[int(path.stem)]

        records = [
            ChunkAnalyzeRecord(plan=plan_a, ok=True,
                               transcripts_path=Path("0")),
            ChunkAnalyzeRecord(plan=plan_b, ok=True,
                               transcripts_path=Path("1")),
        ]
        label_map = {
            LocalSpeakerKey(0, "SPEAKER_00"): "SPEAKER_GLOBAL_00",
            LocalSpeakerKey(1, "SPEAKER_00"): "SPEAKER_GLOBAL_00",
        }
        merged = merge_transcripts(records, label_map, transcripts_loader=_loader)
        assert len(merged) == 2
        assert merged[0].start == 5.0
        assert merged[1].start == 310.0
        assert merged[0].speaker == merged[1].speaker == "SPEAKER_GLOBAL_00"
        # Output is sorted by start.
        assert merged[0].start < merged[1].start

    def test_overlap_duplicates_dropped(self, tmp_path: Path):
        # Plan with overlap — chunk 0 owns [0, 300), chunk 1 owns
        # [300, 600). A duplicate transcript appearing in chunk 1's
        # lead-in (chunk-local t=0..1, global 299..300) must be dropped
        # because its midpoint falls in chunk 0's canonical span.
        from src.voice_pack.chunked import AudioChunkPlan

        plan_a = AudioChunkPlan(0, 0.0, 300.0, 0.0, 301.0)
        plan_b = AudioChunkPlan(1, 300.0, 600.0, 299.0, 600.0)

        def _loader(path: Path):
            return {
                "a": [VoiceChunk(start=295.0, end=300.0, text="boundary",
                                 speaker="SPEAKER_00", confidence=0.9)],
                # In chunk-1 local time, slice_start=299, so a transcript
                # spanning local t=[0, 1] is global [299, 300] — the
                # overlap region. Its midpoint (299.5) belongs to chunk 0.
                "b": [VoiceChunk(start=0.0, end=1.0, text="dup",
                                 speaker="SPEAKER_00", confidence=0.9)],
            }[path.name]

        records = [
            ChunkAnalyzeRecord(plan=plan_a, ok=True, transcripts_path=Path("a")),
            ChunkAnalyzeRecord(plan=plan_b, ok=True, transcripts_path=Path("b")),
        ]
        merged = merge_transcripts(records, {}, transcripts_loader=_loader)
        # Only the chunk-A transcript survives.
        assert len(merged) == 1
        assert merged[0].text == "boundary"

    def test_failed_chunks_skipped(self):
        from src.voice_pack.chunked import AudioChunkPlan

        plan = AudioChunkPlan(0, 0.0, 100.0, 0.0, 100.0)
        record = ChunkAnalyzeRecord(plan=plan, ok=False, error="boom")
        merged = merge_transcripts([record], {})
        assert merged == []

    def test_unmapped_speaker_passes_through(self):
        # When the label_map doesn't carry an entry, the local label
        # is preserved (orchestrator already logged the gap).
        from src.voice_pack.chunked import AudioChunkPlan

        plan = AudioChunkPlan(0, 0.0, 100.0, 0.0, 100.0)

        def _loader(_path):
            return [VoiceChunk(start=0.0, end=10.0, text="hi",
                               speaker="SPEAKER_99", confidence=0.5)]

        record = ChunkAnalyzeRecord(plan=plan, ok=True,
                                    transcripts_path=Path("x"))
        merged = merge_transcripts([record], {}, transcripts_loader=_loader)
        assert merged[0].speaker == "SPEAKER_99"


# ---------------------------------------------------------------------------
# summarize_global_speakers + render
# ---------------------------------------------------------------------------


class TestSummarizeGlobalSpeakers:
    def test_groups_by_speaker_and_sorts_desc(self):
        chunks = [
            VoiceChunk(0, 60, "narrator", "SPEAKER_GLOBAL_00", 0.9),
            VoiceChunk(70, 80, "side", "SPEAKER_GLOBAL_01", 0.8),
            VoiceChunk(100, 120, "narrator2", "SPEAKER_GLOBAL_00", 0.9),
        ]
        out = summarize_global_speakers(chunks)
        assert out[0]["speaker"] == "SPEAKER_GLOBAL_00"
        assert out[0]["chunk_count"] == 2
        assert out[0]["total_seconds"] == pytest.approx(80.0)
        assert out[1]["speaker"] == "SPEAKER_GLOBAL_01"

    def test_empty_input(self):
        assert summarize_global_speakers([]) == []


class TestRenderReport:
    def test_renders_chunks_speakers_and_ambiguous(self):
        from src.voice_pack.chunked import AudioChunkPlan
        from src.voice_pack.reconcile import (
            AmbiguousMerge,
            ReconciliationResult,
        )

        plan = AudioChunkPlan(0, 0.0, 300.0, 0.0, 301.0)
        records = [
            ChunkAnalyzeRecord(plan=plan, ok=True, fallback_used=True),
        ]
        speakers = [
            {"speaker": "SPEAKER_GLOBAL_00", "total_seconds": 120.0,
             "total_minutes": 2.0, "chunk_count": 5,
             "mean_chunk_seconds": 24.0, "quality_tier": "few_shot"},
        ]
        recon = ReconciliationResult(
            ambiguous_pairs=[
                AmbiguousMerge(
                    a=LocalSpeakerKey(0, "SPEAKER_00"),
                    b=LocalSpeakerKey(1, "SPEAKER_00"),
                    similarity=0.71,
                ),
            ],
        )
        report = render_chunked_report(
            input_filename="demo.wav",
            audio_seconds=600.0,
            chunk_records=records,
            speakers=speakers,
            reconciliation=recon,
        )
        assert "demo.wav" in report
        assert "Source duration: 600.0" in report
        assert "cpu fallback" in report
        assert "SPEAKER_GLOBAL_00" in report
        assert "Ambiguous" in report
        assert "0.710" in report


# ---------------------------------------------------------------------------
# run_chunked_analyze — end-to-end
# ---------------------------------------------------------------------------


def _e2e_setup(tmp_path: Path):
    """Build a 900s plan worth of fake artefacts for happy-path tests."""
    out_dir = tmp_path / "out"
    wav = tmp_path / "src.wav"
    wav.write_bytes(b"RIFF...")

    # Three chunks. Chunk 0 has narrator+sidekick, chunk 1 has narrator
    # only, chunk 2 has narrator+stranger. Reconciliation should merge
    # narrator across all three.
    transcripts_by_idx = {
        0: [
            VoiceChunk(5.0, 15.0, "intro", "SPEAKER_00", 0.9),
            VoiceChunk(20.0, 30.0, "side line", "SPEAKER_01", 0.85),
            VoiceChunk(40.0, 50.0, "more", "SPEAKER_00", 0.9),
        ],
        1: [
            VoiceChunk(10.0, 25.0, "narration", "SPEAKER_00", 0.9),
            VoiceChunk(50.0, 65.0, "more narration", "SPEAKER_00", 0.92),
        ],
        2: [
            VoiceChunk(5.0, 18.0, "outro", "SPEAKER_00", 0.9),
            VoiceChunk(60.0, 70.0, "guest", "SPEAKER_01", 0.8),
        ],
    }

    nar = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    side = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    stranger = np.array([0.0, 0.0, 1.0], dtype=np.float64)

    embed_table = {
        (0, "SPEAKER_00"): nar,
        (0, "SPEAKER_01"): side,
        (1, "SPEAKER_00"): nar + 0.01,  # near-identical — merge
        (2, "SPEAKER_00"): nar + 0.01,  # near-identical — merge
        (2, "SPEAKER_01"): stranger,
    }
    return out_dir, wav, transcripts_by_idx, embed_table


class TestRunChunkedAnalyzeE2E:
    def test_happy_path_produces_artifacts(self, tmp_path: Path):
        out_dir, wav, transcripts_by_idx, embed_table = _e2e_setup(tmp_path)
        events: list[ChunkedProgress] = []

        result = run_chunked_analyze(
            wav=wav,
            out_dir=out_dir,
            chunk_seconds=300.0,
            overlap_seconds=0.0,
            workers=1,
            progress_cb=events.append,
            probe_duration_fn=_fake_probe(900.0),
            slice_fn=_fake_slicer(),
            chunk_analyze_fn=_make_chunk_analyse_fn(transcripts_by_idx),
            chunk_embed_fn=_make_embed_fn(embed_table),
        )

        assert result.ok is True
        assert result.return_code == 0
        # Artefacts on disk.
        assert (out_dir / "transcripts.jsonl").exists()
        assert (out_dir / "speakers.yaml").exists()
        assert (out_dir / "report.md").exists()

        # Transcripts globally sorted with global IDs.
        rows = (out_dir / "transcripts.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        assert len(rows) >= 5  # at least the 5 chunk-0/1/2 narrator + side lines
        for row in rows:
            obj = json.loads(row)
            assert obj["speaker"].startswith("SPEAKER_GLOBAL_")
        starts = [json.loads(r)["start"] for r in rows]
        assert starts == sorted(starts)

        # Reconciliation merged the three narrator entries.
        assert result.reconciliation is not None
        narrator_keys = [
            LocalSpeakerKey(0, "SPEAKER_00"),
            LocalSpeakerKey(1, "SPEAKER_00"),
            LocalSpeakerKey(2, "SPEAKER_00"),
        ]
        narrator_globals = {result.reconciliation.label_map[k]
                            for k in narrator_keys}
        assert len(narrator_globals) == 1

        # The biggest cluster (narrator) gets _00.
        assert next(iter(narrator_globals)) == "SPEAKER_GLOBAL_00"

        # Last event is DONE.
        assert events[-1].stage == STAGE_DONE

    def test_cpu_fallback_on_chunk_crash(self, tmp_path: Path):
        out_dir, wav, transcripts_by_idx, embed_table = _e2e_setup(tmp_path)
        events: list[ChunkedProgress] = []

        result = run_chunked_analyze(
            wav=wav,
            out_dir=out_dir,
            chunk_seconds=300.0,
            overlap_seconds=0.0,
            progress_cb=events.append,
            probe_duration_fn=_fake_probe(900.0),
            slice_fn=_fake_slicer(),
            chunk_analyze_fn=_make_chunk_analyse_fn(
                transcripts_by_idx, fail_on_first_call={1},
            ),
            chunk_embed_fn=_make_embed_fn(embed_table),
        )

        assert result.ok is True
        # Chunk 1 should be marked as fallback-used.
        rec1 = result.chunk_records[1]
        assert rec1.ok is True
        assert rec1.fallback_used is True
        # We see both a RETRY and a DONE event for chunk 1.
        retry_events = [e for e in events if e.stage == STAGE_CHUNK_RETRY]
        done_events = [e for e in events
                       if e.stage == STAGE_CHUNK_DONE and e.chunk_index == 1]
        assert any(e.chunk_index == 1 for e in retry_events)
        assert done_events

    def test_chunk_failure_after_fallback_skips_chunk(self, tmp_path: Path):
        out_dir, wav, transcripts_by_idx, _ = _e2e_setup(tmp_path)
        events: list[ChunkedProgress] = []

        # Analyze fake that ALWAYS fails for chunk 1, regardless of fallback.
        def _always_fail_chunk_1(**kwargs):
            chunk_out_dir = Path(kwargs["out_dir"])
            chunk_out_dir.mkdir(parents=True, exist_ok=True)
            idx = int(chunk_out_dir.name.split("_")[-1])
            if idx == 1:
                return _FakeAnalyzeResult(
                    ok=False, out_dir=chunk_out_dir, error="boom",
                )
            chunks = transcripts_by_idx.get(idx, [])
            with (chunk_out_dir / "transcripts.jsonl").open(
                "w", encoding="utf-8",
            ) as fh:
                for c in chunks:
                    fh.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
            return _FakeAnalyzeResult(ok=True, out_dir=chunk_out_dir)

        embeds = {
            (0, "SPEAKER_00"): np.array([1.0, 0.0]),
            (2, "SPEAKER_00"): np.array([1.0, 0.0]) + 0.01,
        }

        result = run_chunked_analyze(
            wav=wav,
            out_dir=out_dir,
            chunk_seconds=300.0,
            overlap_seconds=0.0,
            progress_cb=events.append,
            probe_duration_fn=_fake_probe(900.0),
            slice_fn=_fake_slicer(),
            chunk_analyze_fn=_always_fail_chunk_1,
            chunk_embed_fn=_make_embed_fn(embeds),
        )

        # Run still succeeds — only chunk 1 contributes nothing.
        assert result.ok is True
        rec1 = result.chunk_records[1]
        assert rec1.ok is False
        assert "boom" in (rec1.error or "")
        # We should have a STAGE_CHUNK_FAILED event.
        assert any(e.stage == STAGE_CHUNK_FAILED for e in events)

    def test_all_chunks_fail_returns_error(self, tmp_path: Path):
        out_dir = tmp_path / "out"
        wav = tmp_path / "src.wav"
        wav.write_bytes(b"x")

        def _always_fail(**kwargs):
            chunk_out_dir = Path(kwargs["out_dir"])
            chunk_out_dir.mkdir(parents=True, exist_ok=True)
            return _FakeAnalyzeResult(
                ok=False, out_dir=chunk_out_dir, error="dead",
            )

        result = run_chunked_analyze(
            wav=wav,
            out_dir=out_dir,
            chunk_seconds=300.0,
            overlap_seconds=0.0,
            probe_duration_fn=_fake_probe(900.0),
            slice_fn=_fake_slicer(),
            chunk_analyze_fn=_always_fail,
            chunk_embed_fn=lambda *_a, **_kw: {},
        )
        assert result.ok is False
        assert result.error and "All chunks failed" in result.error

    def test_short_audio_takes_single_chunk_path(self, tmp_path: Path):
        out_dir = tmp_path / "out"
        wav = tmp_path / "src.wav"
        wav.write_bytes(b"x")

        transcripts = {
            0: [VoiceChunk(0.0, 10.0, "hi", "SPEAKER_00", 0.9)],
        }
        embeds = {(0, "SPEAKER_00"): np.array([1.0, 0.0])}

        result = run_chunked_analyze(
            wav=wav,
            out_dir=out_dir,
            chunk_seconds=300.0,
            probe_duration_fn=_fake_probe(60.0),
            slice_fn=_fake_slicer(),
            chunk_analyze_fn=_make_chunk_analyse_fn(transcripts),
            chunk_embed_fn=_make_embed_fn(embeds),
        )
        assert result.ok is True
        assert len(result.chunk_records) == 1

    def test_probe_failure_surfaces_error(self, tmp_path: Path):
        def _bad(_p):
            raise RuntimeError("ffprobe died")
        result = run_chunked_analyze(
            wav=tmp_path / "src.wav",
            out_dir=tmp_path / "out",
            probe_duration_fn=_bad,
            slice_fn=_fake_slicer(),
            chunk_analyze_fn=lambda **_kw: _FakeAnalyzeResult(
                ok=True, out_dir=tmp_path,
            ),
            chunk_embed_fn=lambda *_a, **_kw: {},
        )
        assert result.ok is False
        assert "ffprobe" in (result.error or "")

    def test_progress_events_include_stages_in_order(self, tmp_path: Path):
        out_dir, wav, transcripts_by_idx, embeds = _e2e_setup(tmp_path)
        events: list[ChunkedProgress] = []
        run_chunked_analyze(
            wav=wav,
            out_dir=out_dir,
            chunk_seconds=300.0,
            overlap_seconds=0.0,
            progress_cb=events.append,
            probe_duration_fn=_fake_probe(900.0),
            slice_fn=_fake_slicer(),
            chunk_analyze_fn=_make_chunk_analyse_fn(transcripts_by_idx),
            chunk_embed_fn=_make_embed_fn(embeds),
        )
        stages = [e.stage for e in events]
        # Probe announced before any slice work.
        assert STAGE_PROBE in stages
        assert stages.index(STAGE_PROBE) < stages.index(STAGE_DONE)


# Smoke check that the module re-exports cleanly.
def test_module_smoke_import():
    assert _plan_chunks_re_export is not None
