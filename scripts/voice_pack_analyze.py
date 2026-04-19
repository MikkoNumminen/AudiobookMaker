"""Voice pack analyze CLI — stage 1 of the voice-cloning pipeline.

Given a source audio file, this orchestrator:

1. Runs ASR (transcription) to get time-coded text segments.
2. Runs speaker diarization to get time-coded speaker turns.
3. Intersects the two into per-speaker :class:`VoiceChunk` units.
4. Filters the chunks by duration and confidence.
5. Aggregates per-speaker statistics and assigns a quality tier.

It writes three artefacts to the output directory:

* ``transcripts.jsonl`` — one JSON object per surviving chunk.
* ``speakers.yaml`` — aggregate stats, biggest speaker first.
* ``report.md`` — human-readable summary with a tier legend.

The heavy backends (faster-whisper, pyannote) are reached through
dependency-injected callables ``transcribe_fn`` and ``diarize_fn`` so the
CLI can be tested without GPU/model downloads and swapped for alternate
backends in the future.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

# Make ``src.voice_pack`` importable when running this script directly
# (scripts/ is not a package and is invoked both as a file and as a module).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.ffmpeg_path import setup_ffmpeg_path  # noqa: E402

# Point pydub / faster-whisper at the bundled ffmpeg before any audio
# library import, so voice-pack CLIs work on fresh checkouts where
# ffmpeg isn't on PATH.
setup_ffmpeg_path()

from src.voice_pack.bucket import (  # noqa: E402
    assign_speakers,
    filter_quality,
    summarize_speakers,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.voice_pack.types import AsrSegment, DiarTurn, SpeakerSummary, VoiceChunk


TranscribeFn = Callable[..., "list[AsrSegment]"]
DiarizeFn = Callable[..., "list[DiarTurn]"]


@dataclass
class AnalyzeResult:
    """In-memory return value of :func:`analyze` for tests and callers."""

    chunks: "list[VoiceChunk]"
    speakers: "list[SpeakerSummary]"
    out_dir: Path


# --- Tier legend -----------------------------------------------------------

_TIER_LEGEND = (
    "## Tier legend\n"
    "\n"
    "- **full_lora** - >=30 min of clean audio. Full LoRA fine-tune is worth the GPU cost.\n"
    "- **reduced_lora** - 10-30 min. Reduced-rank LoRA with early stopping (experimental).\n"
    "- **few_shot** - 1-10 min. Extract 3 best ~15 s reference clips instead of training.\n"
    "- **skip** - <1 min. Not enough data to produce a usable voice.\n"
)


def _require_yaml():
    """Lazy import PyYAML with a clear install hint if missing."""
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - trivial guard
        raise RuntimeError(
            "PyYAML is required for voice_pack_analyze. Install with: pip install pyyaml"
        ) from exc
    return yaml


def _write_jsonl(path: Path, chunks: "list[VoiceChunk]") -> None:
    """Write one compact JSON object per line, UTF-8, no ASCII escaping."""
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for chunk in chunks:
            fh.write(json.dumps(chunk.to_dict(), ensure_ascii=False))
            fh.write("\n")


def _write_speakers_yaml(path: Path, speakers: "list[SpeakerSummary]") -> None:
    """Dump aggregate speaker stats, biggest speaker first."""
    yaml = _require_yaml()
    ordered = sorted(speakers, key=lambda s: s.total_seconds, reverse=True)
    payload = [s.to_dict(include_chunks=False) for s in ordered]
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)


def _render_report(
    *,
    input_filename: str,
    absolute_path: Path,
    audio_seconds: float,
    speakers: "list[SpeakerSummary]",
    asr_model: str,
    diarization_model: str,
    min_duration: float,
    max_duration: float,
    min_confidence: float,
) -> str:
    """Build the Markdown report body."""
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

    lines: list[str] = []
    lines.append(f"# Voice pack analysis - {input_filename}")
    lines.append("")
    lines.append(f"Generated {now}.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Speaker | Total minutes | Chunks | Mean chunk (s) | Quality tier |")
    lines.append("|---------|---------------|--------|----------------|--------------|")

    ordered = sorted(speakers, key=lambda s: s.total_seconds, reverse=True)
    for s in ordered:
        minutes = s.total_seconds / 60.0
        lines.append(
            f"| {s.speaker} | {minutes:.1f} | {s.chunk_count} | "
            f"{s.mean_chunk_seconds:.2f} | {s.quality_tier} |"
        )
    if not ordered:
        lines.append("| _(no speakers after filtering)_ |  |  |  |  |")

    lines.append("")
    lines.append(_TIER_LEGEND)
    lines.append("## Parameters")
    lines.append("")
    lines.append(f"- ASR model: {asr_model}")
    lines.append(f"- Diarization model: {diarization_model}")
    lines.append(
        f"- Quality filter: min_duration={min_duration}s, "
        f"max_duration={max_duration}s, min_confidence={min_confidence}"
    )
    lines.append(f"- Source file: {absolute_path}")
    lines.append(f"- Source duration: {audio_seconds} s")
    lines.append("")
    return "\n".join(lines)


def _default_transcribe_fn() -> TranscribeFn:
    from src.voice_pack.asr import transcribe  # noqa: WPS433 — lazy import

    return transcribe


def _select_diarize_fn(name: str) -> DiarizeFn:
    """Return the diarizer backend matching ``name``.

    ``pyannote`` is the default (higher accuracy on typical audiobooks
    when the HF-gated model loads cleanly). ``ecapa`` is the pyannote-free
    fallback: no HF token required, and it has also handled cases where
    pyannote collapsed two similar-timbre readers into one speaker.
    """
    if name == "pyannote":
        from src.voice_pack.diarize import diarize  # noqa: WPS433

        return diarize
    if name == "ecapa":
        from src.voice_pack.diarize_ecapa import diarize_ecapa  # noqa: WPS433

        return diarize_ecapa
    raise ValueError(
        f"unknown diarizer {name!r}; expected 'pyannote' or 'ecapa'"
    )


def _default_diarize_fn() -> DiarizeFn:
    return _select_diarize_fn("pyannote")


_DIARIZATION_MODEL_ID = {
    "pyannote": "pyannote/speaker-diarization-3.1",
    "ecapa": "speechbrain/spkrec-ecapa-voxceleb",
}


def analyze(
    audio_path: "str | Path",
    out_dir: "str | Path",
    *,
    hf_token: "str | None" = None,
    asr_model_size: str = "large-v3",
    asr_device: str = "auto",
    min_duration: float = 1.0,
    max_duration: float = 30.0,
    min_confidence: float = 0.3,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    transcribe_fn: "TranscribeFn | None" = None,
    diarize_fn: "DiarizeFn | None" = None,
    diarizer: str = "pyannote",
    verbose: bool = False,
) -> AnalyzeResult:
    """Run the full analyze pipeline and write artefacts to ``out_dir``.

    Returns the :class:`AnalyzeResult` carrying the in-memory chunks and
    speaker summaries as well — useful for tests and for callers who want
    to post-process without re-parsing the artefacts.

    ``num_speakers`` / ``min_speakers`` / ``max_speakers`` pin the
    diarizer when the operator knows how many readers are in the
    source. Pyannote is good but not perfect at distinguishing similar-
    register readers (two male narrators of similar timbre), and
    telling it the true count avoids the two most common failure
    modes: splitting one reader into ghost speakers, or merging two
    similar readers into one. ``num_speakers`` is exact; min/max are
    ranges; all three unset lets pyannote decide. Exact overrides
    min/max when both are supplied.
    """

    audio_path = Path(audio_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    transcribe_fn = transcribe_fn or _default_transcribe_fn()
    if diarize_fn is None:
        diarize_fn = _select_diarize_fn(diarizer)
    if diarizer not in _DIARIZATION_MODEL_ID:
        raise ValueError(
            f"unknown diarizer {diarizer!r}; expected 'pyannote' or 'ecapa'"
        )

    def _stamp(label: str, t0: float) -> None:
        if verbose:
            print(f"[voice_pack_analyze] {label}: {time.monotonic() - t0:.2f}s")

    t0 = time.monotonic()
    segments = transcribe_fn(audio_path, model_size=asr_model_size, device=asr_device)
    _stamp("asr", t0)

    t0 = time.monotonic()
    diarize_kwargs: dict[str, Any] = {"hf_token": hf_token}
    if num_speakers is not None:
        diarize_kwargs["num_speakers"] = num_speakers
    else:
        if min_speakers is not None:
            diarize_kwargs["min_speakers"] = min_speakers
        if max_speakers is not None:
            diarize_kwargs["max_speakers"] = max_speakers
    turns = diarize_fn(audio_path, **diarize_kwargs)
    _stamp("diarize", t0)

    t0 = time.monotonic()
    all_chunks = assign_speakers(segments, turns)
    chunks = filter_quality(
        all_chunks,
        min_duration=min_duration,
        max_duration=max_duration,
        min_confidence=min_confidence,
    )
    speakers = summarize_speakers(chunks)
    _stamp("bucket", t0)

    # Good-enough audio duration without pulling in ffprobe: last chunk end.
    audio_seconds = float(chunks[-1].end) if chunks else 0.0

    t0 = time.monotonic()
    _write_jsonl(out_dir / "transcripts.jsonl", chunks)
    _write_speakers_yaml(out_dir / "speakers.yaml", speakers)

    report = _render_report(
        input_filename=audio_path.name,
        absolute_path=audio_path.resolve() if audio_path.exists() else audio_path,
        audio_seconds=audio_seconds,
        speakers=speakers,
        asr_model=asr_model_size,
        diarization_model=_DIARIZATION_MODEL_ID[diarizer],
        min_duration=min_duration,
        max_duration=max_duration,
        min_confidence=min_confidence,
    )
    (out_dir / "report.md").write_text(report, encoding="utf-8", newline="\n")
    _stamp("write", t0)

    return AnalyzeResult(chunks=chunks, speakers=speakers, out_dir=out_dir)


# --- CLI -------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice_pack_analyze",
        description=(
            "Analyze a source audio file for voice-cloning data. Runs ASR + "
            "speaker diarization, buckets the results into per-speaker chunks, "
            "and writes transcripts.jsonl, speakers.yaml, and report.md to the "
            "output directory. Use the report to decide which speakers are "
            "worth fine-tuning a LoRA for and which should fall back to "
            "few-shot reference clips."
        ),
    )
    parser.add_argument(
        "--input",
        "-i",
        dest="input",
        required=False,
        help="Path to the source audio file (wav/mp3/ogg/...).",
    )
    parser.add_argument(
        "positional_input",
        nargs="?",
        metavar="AUDIO",
        help="Positional alternative to --input.",
    )
    parser.add_argument(
        "--out",
        "-o",
        dest="out",
        required=True,
        help="Output directory. Created if missing.",
    )
    parser.add_argument(
        "--hf-token",
        dest="hf_token",
        default=None,
        help=(
            "HuggingFace access token for the diarization pipeline. "
            "Defaults to the HF_TOKEN environment variable when unset."
        ),
    )
    parser.add_argument(
        "--asr-model",
        dest="asr_model",
        default="large-v3",
        help="faster-whisper model size. Default: large-v3.",
    )
    parser.add_argument(
        "--asr-device",
        dest="asr_device",
        default="auto",
        help="Device for ASR ('auto', 'cuda', 'cpu'). Default: auto.",
    )
    parser.add_argument(
        "--min-duration",
        dest="min_duration",
        type=float,
        default=1.0,
        help="Drop chunks shorter than this (seconds). Default: 1.0.",
    )
    parser.add_argument(
        "--max-duration",
        dest="max_duration",
        type=float,
        default=30.0,
        help="Drop chunks longer than this (seconds). Default: 30.0.",
    )
    parser.add_argument(
        "--min-confidence",
        dest="min_confidence",
        type=float,
        default=0.3,
        help="Drop chunks below this ASR confidence. Default: 0.3.",
    )
    parser.add_argument(
        "--num-speakers",
        dest="num_speakers",
        type=int,
        default=None,
        help=(
            "Exact number of readers in the source (pyannote hint). "
            "Use when you know the cast size, e.g. '1' for a solo "
            "narrator or '6' for a full-cast production. Overrides "
            "--min-speakers / --max-speakers when set."
        ),
    )
    parser.add_argument(
        "--min-speakers",
        dest="min_speakers",
        type=int,
        default=None,
        help="Lower bound on reader count (pyannote hint).",
    )
    parser.add_argument(
        "--max-speakers",
        dest="max_speakers",
        type=int,
        default=None,
        help="Upper bound on reader count (pyannote hint).",
    )
    parser.add_argument(
        "--diarizer",
        dest="diarizer",
        choices=("pyannote", "ecapa"),
        default="pyannote",
        help=(
            "Diarization backend. 'pyannote' (default) is higher quality "
            "on typical audiobooks but requires an accepted HF license + "
            "HF_TOKEN. 'ecapa' uses speechbrain ECAPA-TDNN + agglomerative "
            "clustering — no HF gating, and has rescued runs where pyannote "
            "collapsed two similar-timbre readers into one speaker."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        action="store_true",
        help="Print per-stage timing to stdout.",
    )
    return parser


def _resolve_input(args: argparse.Namespace) -> str:
    if args.input and args.positional_input:
        raise SystemExit("Provide audio via --input OR positional argument, not both.")
    chosen = args.input or args.positional_input
    if not chosen:
        raise SystemExit("Missing input audio: pass --input PATH or a positional path.")
    return chosen


def main(argv: "list[str] | None" = None) -> int:
    """argparse-driven entry point. Returns 0 on success, 1 on failure."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 on bad args; preserve its code for scripts but
        # funnel any manual SystemExit("msg") through the failure path.
        code = exc.code if isinstance(exc.code, int) else 1
        if exc.code and not isinstance(exc.code, int):
            print(str(exc.code), file=sys.stderr)
            return 1
        return code

    try:
        input_path = _resolve_input(args)
        hf_token = args.hf_token or os.environ.get("HF_TOKEN")
        result = analyze(
            audio_path=input_path,
            out_dir=args.out,
            hf_token=hf_token,
            asr_model_size=args.asr_model,
            asr_device=args.asr_device,
            min_duration=args.min_duration,
            max_duration=args.max_duration,
            min_confidence=args.min_confidence,
            num_speakers=args.num_speakers,
            min_speakers=args.min_speakers,
            max_speakers=args.max_speakers,
            diarizer=args.diarizer,
            verbose=args.verbose,
        )
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - top-level CLI guard
        print(f"voice_pack_analyze failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Analysis complete. Wrote {len(result.chunks)} chunks across "
        f"{len(result.speakers)} speakers to {result.out_dir}"
    )
    return 0


if __name__ == "__main__":
    import sys as _sys

    _sys.exit(main())
