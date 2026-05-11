"""Chunked-analyze orchestrator.

This module is the answer to the long-source crash described in
:mod:`src.voice_pack.chunked`: instead of feeding a 1 h audiobook to
faster-whisper + pyannote in one shot (which trips a Windows native
fast-fail crash, exit code ``0xC0000409`` / 127), we slice the source
into safe-sized chunks, analyse each one as a separate child
subprocess, reconcile speakers across chunks, and emit a single
canonical set of artefacts (``transcripts.jsonl``, ``speakers.yaml``,
``report.md``) — exactly what the single-shot analyser writes — so
every downstream consumer (reference picker, voice-pack export,
voice-pack train) keeps working without changes.

Pipeline:

1. ``ffprobe`` the source duration.
2. :func:`src.voice_pack.chunked.plan_chunks` builds a slice plan.
3. ffmpeg cuts each chunk into a 16 kHz mono WAV inside a scratch
   subdirectory of ``out_dir``.
4. The single-shot analyser (:mod:`src.voice_pack_subproc`) runs once
   per chunk. A configurable ``--workers`` flag lets the operator
   parallelise; the default of 1 is the GPU-safe choice on a 12 GB
   card. A semaphore gates concurrency.
5. If a chunk subprocess crashes (return-code != 0, no artefacts on
   disk), it gets retried once with ``--asr-device cpu`` — slower but
   immune to the GPU-side native crash. Both failures = skip the
   chunk and log it.
6. Per-chunk transcripts are loaded, time-shifted into the source
   timeline, and overlap duplicates dropped via
   :func:`src.voice_pack.chunked.chunk_owns_timestamp`.
7. Per-chunk speakers are embedded (Chatterbox voice encoder) and
   :func:`src.voice_pack.reconcile.reconcile_speakers` produces a
   ``LocalSpeakerKey -> SPEAKER_GLOBAL_NN`` map.
8. Every per-chunk transcript is rewritten with global speaker IDs,
   then bucketed/summarised by the existing
   :mod:`src.voice_pack.bucket` helpers and written out.

Every I/O boundary is dependency-injected so tests run end-to-end
without ffmpeg, without subprocess fork, and without any model.
"""

from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from src.voice_pack.bucket import classify_quality_tier
from src.voice_pack.chunked import (
    DEFAULT_CHUNK_SECONDS,
    DEFAULT_OVERLAP_SECONDS,
    AudioChunkPlan,
    chunk_owns_timestamp,
    globalise_time,
    plan_chunks,
)
from src.voice_pack.ffmpeg_slice import (
    FfmpegError,
    SliceRequest,
    probe_duration,
    slice_audio,
)
from src.voice_pack.reconcile import (
    DEFAULT_AMBIGUOUS_THRESHOLD,
    DEFAULT_HARD_MERGE_THRESHOLD,
    AmbiguousMerge,
    LocalSpeakerKey,
    ReconciliationResult,
    reconcile_speakers,
)
from src.voice_pack.types import VoiceChunk

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


# Stage identifiers re-emitted to callers as progress events. Kept as
# bare strings (not an enum) because the upstream voice_pack_subproc
# module uses the same convention and the GUI dispatches on string
# matches.
STAGE_STARTING: str = "chunked_starting"
STAGE_PROBE: str = "chunked_probe"
STAGE_PLANNED: str = "chunked_planned"
STAGE_SLICING: str = "chunked_slicing"
STAGE_CHUNK_START: str = "chunked_chunk_start"
STAGE_CHUNK_LINE: str = "chunked_chunk_line"
STAGE_CHUNK_DONE: str = "chunked_chunk_done"
STAGE_CHUNK_RETRY: str = "chunked_chunk_retry"
STAGE_CHUNK_FAILED: str = "chunked_chunk_failed"
STAGE_RECONCILE: str = "chunked_reconcile"
STAGE_MERGE: str = "chunked_merge"
STAGE_DONE: str = "chunked_done"
STAGE_ERROR: str = "chunked_error"


@dataclass(frozen=True)
class ChunkedProgress:
    """One progress event streamed by :func:`run_chunked_analyze`.

    ``stage`` is one of the ``STAGE_*`` constants above. ``message``
    is a plain-text one-liner safe to dump to a log box. ``chunk_index``
    is set on per-chunk events; ``extra`` carries optional structured
    payload (e.g. duration on PROBE).
    """

    stage: str
    message: str
    chunk_index: Optional[int] = None
    extra: dict = field(default_factory=dict)


@dataclass
class ChunkAnalyzeRecord:
    """Outcome of one chunk's analyse run.

    ``ok`` False means we couldn't recover any artefacts after the
    retry — those chunks contribute nothing to the merged manifest
    but their timeline span is still announced in the run report so
    the operator can see the gap.
    """

    plan: AudioChunkPlan
    ok: bool
    transcripts_path: Optional[Path] = None
    speakers_yaml_path: Optional[Path] = None
    error: Optional[str] = None
    fallback_used: bool = False
    chunk_dir: Optional[Path] = None


@dataclass
class ChunkedAnalyzeResult:
    """In-memory return value of :func:`run_chunked_analyze`."""

    ok: bool
    return_code: int
    transcripts_path: Path
    speakers_yaml_path: Path
    report_path: Path
    chunk_records: list[ChunkAnalyzeRecord] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    reconciliation: Optional[ReconciliationResult] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Injectable I/O — defaults shell out, tests pass fakes
# ---------------------------------------------------------------------------


#: ``(plan, chunk_wav, out_dir, progress_cb, asr_device, ...) -> AnalyzeJobResult``.
#: The default routes through :func:`src.voice_pack_subproc.run_analyze`.
ChunkAnalyzeFn = Callable[..., Any]

#: ``(chunk_wav, transcripts_path) -> dict[str, np.ndarray]`` — one mean
#: voice embedding per local speaker found in the chunk. Default uses
#: Chatterbox's voice encoder; tests pass a synthetic embedder.
ChunkEmbedFn = Callable[[Path, Path], dict[str, Any]]


def _default_chunk_analyze_fn(**kwargs: Any) -> Any:  # pragma: no cover - integration shim
    from src.voice_pack_subproc import run_analyze

    return run_analyze(**kwargs)


def _default_chunk_embed_fn(
    chunk_wav: Path, transcripts_path: Path,
) -> dict[str, Any]:  # pragma: no cover - integration shim
    """Embed each local speaker's longest chunk via Chatterbox VE.

    Each speaker's longest single transcript chunk is sliced from the
    chunk WAV and fed through ``engine.ve.embeds_from_wavs`` to get a
    256-d embedding. We use the longest chunk (not a centroid of all
    chunks) because:

    * Reconciliation only needs to recognise whether the speaker also
      appears in another chunk; one clean embedding is enough.
    * The embedder is the slowest step. Linear in chunk count = bad.
    """
    import numpy as np  # type: ignore

    from src.voice_pack.characters import _default_embedder  # noqa: WPS437 - private but stable

    chunks_by_speaker: dict[str, list[VoiceChunk]] = defaultdict(list)
    with transcripts_path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            obj = json.loads(raw)
            chunks_by_speaker[obj["speaker"]].append(
                VoiceChunk(
                    start=float(obj["start"]),
                    end=float(obj["end"]),
                    text=str(obj["text"]),
                    speaker=str(obj["speaker"]),
                    confidence=float(obj["confidence"]),
                )
            )

    if not chunks_by_speaker:
        return {}

    from pydub import AudioSegment  # type: ignore[import-not-found]

    audio = AudioSegment.from_file(str(chunk_wav)).set_channels(1).set_frame_rate(16000)
    embed = _default_embedder()
    out: dict[str, Any] = {}
    for speaker, chunks in chunks_by_speaker.items():
        longest = max(chunks, key=lambda c: c.duration)
        clip = audio[int(longest.start * 1000): int(longest.end * 1000)]
        samples = np.array(clip.get_array_of_samples(), dtype=np.float32)
        if clip.sample_width == 2:
            samples = samples / 32768.0
        out[speaker] = embed(samples, 16000)
    return out


# ---------------------------------------------------------------------------
# Pure helpers (testable directly)
# ---------------------------------------------------------------------------


def merge_transcripts(
    chunk_records: Sequence[ChunkAnalyzeRecord],
    label_map: dict[LocalSpeakerKey, str],
    *,
    transcripts_loader: Optional[Callable[[Path], list[VoiceChunk]]] = None,
) -> list[VoiceChunk]:
    """Merge per-chunk transcripts into a single ordered list.

    Each transcript's timestamps are shifted into the source timeline
    via :func:`globalise_time`. Transcripts whose midpoint falls
    outside the chunk's canonical span (``[start_global, end_global)``)
    are dropped — they're duplicates from the overlap region with the
    neighbouring chunk.

    Speaker labels are rewritten via ``label_map`` when an entry
    exists; otherwise the local label is kept (the orchestrator
    flagged that case via :data:`STAGE_RECONCILE` for the operator).
    """
    loader = transcripts_loader or _load_chunk_transcripts
    merged: list[VoiceChunk] = []
    for record in chunk_records:
        if not record.ok or record.transcripts_path is None:
            continue
        for raw_chunk in loader(record.transcripts_path):
            global_start = globalise_time(record.plan, raw_chunk.start)
            global_end = globalise_time(record.plan, raw_chunk.end)
            midpoint = (global_start + global_end) * 0.5
            if not chunk_owns_timestamp(record.plan, midpoint):
                continue  # duplicate from the overlap region
            local_key = LocalSpeakerKey(
                chunk_index=record.plan.index,
                local_speaker=raw_chunk.speaker,
            )
            new_speaker = label_map.get(local_key, raw_chunk.speaker)
            merged.append(
                VoiceChunk(
                    start=global_start,
                    end=global_end,
                    text=raw_chunk.text,
                    speaker=new_speaker,
                    confidence=raw_chunk.confidence,
                    character=raw_chunk.character,
                )
            )
    merged.sort(key=lambda c: c.start)
    return merged


def _load_chunk_transcripts(path: Path) -> list[VoiceChunk]:
    """Load every VoiceChunk from a per-chunk transcripts.jsonl.

    Mirrors :func:`scripts.voice_pack_export.load_transcripts` but
    inlined here to keep this module independent of the script
    package layout (scripts/ is not importable).
    """
    chunks: list[VoiceChunk] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                character = obj.get("character")
                chunks.append(
                    VoiceChunk(
                        start=float(obj["start"]),
                        end=float(obj["end"]),
                        text=str(obj["text"]),
                        speaker=str(obj["speaker"]),
                        confidence=float(obj["confidence"]),
                        character=str(character) if character is not None else None,
                    )
                )
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"{path}:{line_no}: could not parse VoiceChunk row — {exc}"
                ) from exc
    return chunks


def summarize_global_speakers(
    chunks: Sequence[VoiceChunk],
) -> list[dict[str, Any]]:
    """Aggregate merged transcripts into the speakers.yaml shape.

    Mirrors :func:`src.voice_pack.bucket.summarize_speakers` but
    keeping the output in raw-dict form so the orchestrator can write
    it to disk without importing yaml here.
    """
    grouped: dict[str, list[VoiceChunk]] = defaultdict(list)
    for chunk in chunks:
        grouped[chunk.speaker].append(chunk)

    summaries: list[dict[str, Any]] = []
    for speaker, speaker_chunks in grouped.items():
        total_seconds = sum(c.duration for c in speaker_chunks)
        chunk_count = len(speaker_chunks)
        mean_chunk_seconds = total_seconds / chunk_count if chunk_count else 0.0
        summaries.append(
            {
                "speaker": speaker,
                "total_seconds": total_seconds,
                "total_minutes": round(total_seconds / 60.0, 2),
                "chunk_count": chunk_count,
                "mean_chunk_seconds": mean_chunk_seconds,
                "quality_tier": classify_quality_tier(total_seconds),
            }
        )
    summaries.sort(key=lambda d: d["total_seconds"], reverse=True)
    return summaries


def render_chunked_report(
    *,
    input_filename: str,
    audio_seconds: float,
    chunk_records: Sequence[ChunkAnalyzeRecord],
    speakers: Sequence[dict[str, Any]],
    reconciliation: ReconciliationResult,
) -> str:
    """Build the human-readable report.md body for a chunked run.

    Includes a per-chunk status table, the global speaker summary, and
    a flagged section listing ambiguous reconciliations the operator
    should look at.
    """
    lines: list[str] = []
    lines.append(f"# Chunked voice-pack analysis - {input_filename}")
    lines.append("")
    lines.append(f"Source duration: {audio_seconds:.1f} s "
                 f"({audio_seconds / 60.0:.1f} min).")
    lines.append("")

    lines.append("## Chunks")
    lines.append("")
    lines.append("| # | Start (s) | End (s) | Status | Notes |")
    lines.append("|---|-----------|---------|--------|-------|")
    for record in chunk_records:
        status = "ok" if record.ok else "FAILED"
        notes = []
        if record.fallback_used:
            notes.append("cpu fallback")
        if record.error:
            notes.append(record.error[:60])
        lines.append(
            f"| {record.plan.index} | {record.plan.start_global:.1f} | "
            f"{record.plan.end_global:.1f} | {status} | {' / '.join(notes)} |"
        )
    lines.append("")

    lines.append("## Global speakers")
    lines.append("")
    lines.append("| Speaker | Total minutes | Chunks | Mean chunk (s) | "
                 "Quality tier |")
    lines.append("|---------|---------------|--------|----------------|"
                 "--------------|")
    for spk in speakers:
        minutes = spk["total_seconds"] / 60.0
        lines.append(
            f"| {spk['speaker']} | {minutes:.1f} | "
            f"{spk['chunk_count']} | "
            f"{spk['mean_chunk_seconds']:.2f} | "
            f"{spk['quality_tier']} |"
        )
    if not speakers:
        lines.append("| _(no speakers after filtering)_ |  |  |  |  |")
    lines.append("")

    if reconciliation.ambiguous_pairs:
        lines.append("## Ambiguous merges (please review)")
        lines.append("")
        lines.append("These cross-chunk pairs landed in the grey band — "
                     "we did not merge them, but they may be the same "
                     "person.")
        lines.append("")
        lines.append("| Chunk A | Speaker A | Chunk B | Speaker B | "
                     "Cosine sim |")
        lines.append("|---------|-----------|---------|-----------|"
                     "------------|")
        for pair in reconciliation.ambiguous_pairs:
            lines.append(
                f"| {pair.a.chunk_index} | {pair.a.local_speaker} | "
                f"{pair.b.chunk_index} | {pair.b.local_speaker} | "
                f"{pair.similarity:.3f} |"
            )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subprocess orchestration
# ---------------------------------------------------------------------------


def _emit(progress_cb: Optional[Callable[[ChunkedProgress], None]],
          event: ChunkedProgress) -> None:
    if progress_cb is None:
        return
    progress_cb(event)


def _run_one_chunk(
    *,
    plan: AudioChunkPlan,
    chunk_wav: Path,
    out_dir: Path,
    chunk_analyze_fn: ChunkAnalyzeFn,
    progress_cb: Optional[Callable[[ChunkedProgress], None]],
    cancel_event: Optional[threading.Event],
    cuda_semaphore: threading.Semaphore,
    analyze_kwargs: dict,
    log_lines: list[str],
) -> ChunkAnalyzeRecord:
    """Run analyse for a single chunk with the GPU-fallback retry.

    The semaphore gates concurrent CUDA jobs. The first attempt
    inherits ``analyze_kwargs`` (typically ``asr_device='auto'``);
    if it returns non-OK we retry once with ``asr_device='cpu'``.

    All progress / log lines are forwarded to ``progress_cb`` and
    appended to ``log_lines`` so the run report carries everything.
    """

    record = ChunkAnalyzeRecord(plan=plan, ok=False, chunk_dir=out_dir)

    def _chunk_progress(event: Any) -> None:
        text = getattr(event, "message", str(event))
        log_lines.append(f"[chunk {plan.index}] {text}")
        _emit(progress_cb, ChunkedProgress(
            stage=STAGE_CHUNK_LINE,
            message=text,
            chunk_index=plan.index,
        ))

    def _attempt(asr_device: str) -> Any:
        kwargs = dict(analyze_kwargs)
        kwargs["wav"] = chunk_wav
        kwargs["out_dir"] = out_dir
        kwargs["progress_cb"] = _chunk_progress
        kwargs["cancel_event"] = cancel_event
        if asr_device:
            kwargs["env_overrides"] = dict(kwargs.get("env_overrides") or {})
            # The CLI honours --asr-device; pass it through the kwargs
            # the subproc layer accepts. We append to the argv via the
            # extra_argv channel so we don't have to change run_analyze
            # signatures.
            kwargs["extra_argv"] = list(kwargs.get("extra_argv") or [])
            kwargs["extra_argv"] += ["--asr-device", asr_device]
        return chunk_analyze_fn(**kwargs)

    _emit(progress_cb, ChunkedProgress(
        stage=STAGE_CHUNK_START,
        message=f"Analysing chunk {plan.index} "
                f"({plan.slice_duration:.1f}s slice)…",
        chunk_index=plan.index,
    ))

    with cuda_semaphore:
        first = _attempt(asr_device=analyze_kwargs.get("asr_device") or "")
    ok = bool(getattr(first, "ok", False))
    if ok:
        record.ok = True
        record.transcripts_path = getattr(first, "transcripts_path", None)
        record.speakers_yaml_path = getattr(first, "speakers_yaml_path", None)
        _emit(progress_cb, ChunkedProgress(
            stage=STAGE_CHUNK_DONE,
            message=f"Chunk {plan.index} OK.",
            chunk_index=plan.index,
        ))
        return record

    # Retry path: GPU crashes are precisely the failure mode the
    # cpu device escapes. Retry once.
    err = getattr(first, "error", None) or "unknown failure"
    record.error = err
    _emit(progress_cb, ChunkedProgress(
        stage=STAGE_CHUNK_RETRY,
        message=f"Chunk {plan.index} failed ({err}); "
                f"retrying with --asr-device cpu.",
        chunk_index=plan.index,
    ))
    second = _attempt(asr_device="cpu")
    if getattr(second, "ok", False):
        record.ok = True
        record.fallback_used = True
        record.error = None
        record.transcripts_path = getattr(second, "transcripts_path", None)
        record.speakers_yaml_path = getattr(second, "speakers_yaml_path", None)
        _emit(progress_cb, ChunkedProgress(
            stage=STAGE_CHUNK_DONE,
            message=f"Chunk {plan.index} OK on cpu fallback.",
            chunk_index=plan.index,
        ))
        return record

    err2 = getattr(second, "error", None) or "unknown failure"
    record.error = f"first: {err} ; cpu fallback: {err2}"
    _emit(progress_cb, ChunkedProgress(
        stage=STAGE_CHUNK_FAILED,
        message=f"Chunk {plan.index} failed twice — skipping. {record.error}",
        chunk_index=plan.index,
    ))
    return record


def _slice_all(
    *,
    source: Path,
    plans: Iterable[AudioChunkPlan],
    out_dir: Path,
    slice_fn: Callable[..., Path],
    progress_cb: Optional[Callable[[ChunkedProgress], None]],
) -> dict[int, Path]:
    """ffmpeg-cut every planned chunk into ``out_dir/chunks``.

    Sequential (not parallel) — ffmpeg is I/O-bound and the local
    disk is the bottleneck; running parallel ffmpegs typically
    *slows down* total wall time on a single SSD.
    """
    chunk_dir = out_dir / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[int, Path] = {}
    for plan in plans:
        out_path = chunk_dir / f"chunk_{plan.index:03d}.wav"
        slice_fn(SliceRequest(
            source=source,
            out_path=out_path,
            start_seconds=plan.slice_start,
            end_seconds=plan.slice_end,
        ))
        paths[plan.index] = out_path
        _emit(progress_cb, ChunkedProgress(
            stage=STAGE_SLICING,
            message=f"Sliced chunk {plan.index} "
                    f"({plan.slice_duration:.1f}s).",
            chunk_index=plan.index,
        ))
    return paths


def _embed_chunks(
    *,
    chunk_records: Sequence[ChunkAnalyzeRecord],
    chunk_paths: dict[int, Path],
    chunk_embed_fn: ChunkEmbedFn,
    progress_cb: Optional[Callable[[ChunkedProgress], None]],
) -> tuple[list[LocalSpeakerKey], list[Any], dict[LocalSpeakerKey, float]]:
    """Embed each (chunk, local_speaker) pair, returning rows for
    :func:`reconcile_speakers` plus per-key durations.

    Skips chunks that didn't produce a transcripts.jsonl (failed ones).
    """
    keys: list[LocalSpeakerKey] = []
    embeddings: list[Any] = []
    durations: dict[LocalSpeakerKey, float] = {}

    for record in chunk_records:
        if not record.ok or record.transcripts_path is None:
            continue
        chunk_wav = chunk_paths.get(record.plan.index)
        if chunk_wav is None or not chunk_wav.exists():
            continue
        try:
            speaker_to_emb = chunk_embed_fn(chunk_wav, record.transcripts_path)
        except Exception as exc:  # noqa: BLE001 - log + skip
            _emit(progress_cb, ChunkedProgress(
                stage=STAGE_CHUNK_FAILED,
                message=f"Embedding chunk {record.plan.index} failed: {exc}",
                chunk_index=record.plan.index,
            ))
            continue
        # Compute per-(chunk, speaker) total seconds from the
        # transcripts so duration ranking is honest.
        chunk_duration_by_speaker: dict[str, float] = defaultdict(float)
        for vc in _load_chunk_transcripts(record.transcripts_path):
            chunk_duration_by_speaker[vc.speaker] += vc.duration

        for local_speaker, emb in speaker_to_emb.items():
            key = LocalSpeakerKey(
                chunk_index=record.plan.index,
                local_speaker=local_speaker,
            )
            keys.append(key)
            embeddings.append(emb)
            durations[key] = chunk_duration_by_speaker.get(local_speaker, 0.0)
    return keys, embeddings, durations


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_chunked_analyze(
    wav: Path,
    out_dir: Path,
    *,
    chunk_seconds: float = DEFAULT_CHUNK_SECONDS,
    overlap_seconds: float = DEFAULT_OVERLAP_SECONDS,
    workers: int = 1,
    hard_merge_threshold: float = DEFAULT_HARD_MERGE_THRESHOLD,
    ambiguous_threshold: float = DEFAULT_AMBIGUOUS_THRESHOLD,
    num_speakers: Optional[int] = None,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
    diarizer: str = "pyannote",
    hf_token: Optional[str] = None,
    asr_device: str = "auto",
    progress_cb: Optional[Callable[[ChunkedProgress], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    # injected I/O
    probe_duration_fn: Callable[[Path], float] = probe_duration,
    slice_fn: Callable[[SliceRequest], Path] = slice_audio,
    chunk_analyze_fn: Optional[ChunkAnalyzeFn] = None,
    chunk_embed_fn: Optional[ChunkEmbedFn] = None,
) -> ChunkedAnalyzeResult:
    """Analyse a long source by chunking and stitching.

    On success the output directory holds the same three artefacts
    the single-shot analyser writes (``transcripts.jsonl``,
    ``speakers.yaml``, ``report.md``) plus a ``chunks/`` subdirectory
    with the cut WAVs and per-chunk artefacts. The wav files live in
    ``chunks/`` so the operator can re-run a single-chunk analyse
    against them for debugging without re-slicing the source.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk_analyze_fn = chunk_analyze_fn or _default_chunk_analyze_fn
    chunk_embed_fn = chunk_embed_fn or _default_chunk_embed_fn

    log_lines: list[str] = []
    result = ChunkedAnalyzeResult(
        ok=False,
        return_code=-1,
        transcripts_path=out_dir / "transcripts.jsonl",
        speakers_yaml_path=out_dir / "speakers.yaml",
        report_path=out_dir / "report.md",
    )

    _emit(progress_cb, ChunkedProgress(
        stage=STAGE_STARTING,
        message="Starting chunked analyze.",
    ))

    # ------ probe ------
    try:
        total_seconds = probe_duration_fn(wav)
    except Exception as exc:  # noqa: BLE001 - top-level guard
        msg = f"ffprobe failed on the source: {exc}"
        result.error = msg
        _emit(progress_cb, ChunkedProgress(stage=STAGE_ERROR, message=msg))
        return result
    _emit(progress_cb, ChunkedProgress(
        stage=STAGE_PROBE,
        message=f"Source is {total_seconds:.1f}s "
                f"({total_seconds / 60.0:.1f} min).",
        extra={"duration_seconds": total_seconds},
    ))

    # ------ plan ------
    plans = plan_chunks(
        total_seconds,
        chunk_seconds=chunk_seconds,
        overlap_seconds=overlap_seconds,
    )
    if not plans:
        msg = "No chunks planned (audio too short or empty)."
        result.error = msg
        _emit(progress_cb, ChunkedProgress(stage=STAGE_ERROR, message=msg))
        return result
    _emit(progress_cb, ChunkedProgress(
        stage=STAGE_PLANNED,
        message=f"Planned {len(plans)} chunk(s) of "
                f"~{chunk_seconds:.0f}s each.",
        extra={"chunk_count": len(plans)},
    ))

    # ------ slice ------
    try:
        chunk_paths = _slice_all(
            source=wav,
            plans=plans,
            out_dir=out_dir,
            slice_fn=slice_fn,
            progress_cb=progress_cb,
        )
    except (FfmpegError, ValueError) as exc:
        msg = f"ffmpeg slicing failed: {exc}"
        result.error = msg
        _emit(progress_cb, ChunkedProgress(stage=STAGE_ERROR, message=msg))
        return result

    # ------ analyse each chunk ------
    cuda_semaphore = threading.Semaphore(max(1, workers))
    analyze_kwargs: dict[str, Any] = {
        "num_speakers": num_speakers,
        "min_speakers": min_speakers,
        "max_speakers": max_speakers,
        "diarizer": diarizer,
        "hf_token": hf_token,
        "asr_device": asr_device,
    }

    chunk_records: list[ChunkAnalyzeRecord] = [None] * len(plans)  # type: ignore[list-item]
    chunks_root = out_dir / "chunks"

    def _job(plan: AudioChunkPlan) -> ChunkAnalyzeRecord:
        chunk_out_dir = chunks_root / f"chunk_{plan.index:03d}"
        chunk_out_dir.mkdir(parents=True, exist_ok=True)
        return _run_one_chunk(
            plan=plan,
            chunk_wav=chunk_paths[plan.index],
            out_dir=chunk_out_dir,
            chunk_analyze_fn=chunk_analyze_fn,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
            cuda_semaphore=cuda_semaphore,
            analyze_kwargs=analyze_kwargs,
            log_lines=log_lines,
        )

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_job, plan): plan for plan in plans}
            for fut in as_completed(futures):
                rec = fut.result()
                chunk_records[rec.plan.index] = rec
    else:
        for plan in plans:
            chunk_records[plan.index] = _job(plan)

    # ------ embed + reconcile ------
    _emit(progress_cb, ChunkedProgress(
        stage=STAGE_RECONCILE,
        message="Reconciling speakers across chunks…",
    ))
    keys, embeddings, durations = _embed_chunks(
        chunk_records=chunk_records,
        chunk_paths=chunk_paths,
        chunk_embed_fn=chunk_embed_fn,
        progress_cb=progress_cb,
    )
    if keys:
        reconciliation = reconcile_speakers(
            keys=keys,
            embeddings=embeddings,
            durations=durations,
            hard_merge_threshold=hard_merge_threshold,
            ambiguous_threshold=ambiguous_threshold,
        )
    else:
        reconciliation = ReconciliationResult()
    result.reconciliation = reconciliation

    # ------ merge ------
    _emit(progress_cb, ChunkedProgress(
        stage=STAGE_MERGE,
        message="Merging per-chunk transcripts.",
    ))
    merged_chunks = merge_transcripts(chunk_records, reconciliation.label_map)
    speaker_summaries = summarize_global_speakers(merged_chunks)
    write_artefacts(
        out_dir=out_dir,
        merged_chunks=merged_chunks,
        speaker_summaries=speaker_summaries,
        report=render_chunked_report(
            input_filename=wav.name,
            audio_seconds=total_seconds,
            chunk_records=chunk_records,
            speakers=speaker_summaries,
            reconciliation=reconciliation,
        ),
    )

    result.chunk_records = list(chunk_records)
    result.log_lines = log_lines

    failed_count = sum(1 for r in chunk_records if not r.ok)
    if failed_count == len(chunk_records):
        msg = "All chunks failed — no merged output."
        result.error = msg
        result.return_code = 1
        _emit(progress_cb, ChunkedProgress(stage=STAGE_ERROR, message=msg))
        return result

    result.ok = True
    result.return_code = 0
    _emit(progress_cb, ChunkedProgress(
        stage=STAGE_DONE,
        message=(
            f"Chunked analyze finished. "
            f"{len(chunk_records) - failed_count}/{len(chunk_records)} "
            f"chunks ok; {len(speaker_summaries)} global speaker(s)."
        ),
    ))
    return result


def write_artefacts(
    *,
    out_dir: Path,
    merged_chunks: Sequence[VoiceChunk],
    speaker_summaries: Sequence[dict[str, Any]],
    report: str,
) -> None:
    """Write the three canonical artefacts to ``out_dir``.

    Mirrors the writes done by ``scripts/voice_pack_analyze.analyze``
    so downstream consumers of ``transcripts.jsonl`` /
    ``speakers.yaml`` / ``report.md`` see the exact same shape they
    would after a single-shot run.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    transcripts_path = out_dir / "transcripts.jsonl"
    with transcripts_path.open("w", encoding="utf-8", newline="\n") as fh:
        for chunk in merged_chunks:
            fh.write(json.dumps(chunk.to_dict(), ensure_ascii=False))
            fh.write("\n")

    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - dev-only error
        raise RuntimeError(
            "PyYAML is required for chunked analyze. "
            "Install with: pip install pyyaml"
        ) from exc
    speakers_path = out_dir / "speakers.yaml"
    with speakers_path.open("w", encoding="utf-8", newline="\n") as fh:
        yaml.safe_dump(
            list(speaker_summaries),
            fh,
            sort_keys=False,
            allow_unicode=True,
        )

    (out_dir / "report.md").write_text(report, encoding="utf-8", newline="\n")
