"""Voice pack dataset-export CLI — stage 2 bridge.

Reads the ``transcripts.jsonl`` produced by :mod:`voice_pack_analyze` plus
the original source audio file, filters to a single speaker, promotes the
:class:`VoiceChunk` objects to :class:`TaggedChunk` (with a default
``neutral`` emotion label for now), and calls
:func:`src.voice_pack.dataset.export_dataset` to slice per-clip WAVs and
write ``manifest.json`` ready for :mod:`voice_pack_train`.

This is the thin glue that was missing between the analyze and train
stages of the voice-pack pipeline. The hand-rolled Python snippet in
``docs/voice_pack_training.md`` was its predecessor.

Emotion tagging is intentionally trivial in this first version — every
chunk gets labelled ``neutral``. A real emotion classifier plugs in via
``--emotion-label`` (for a flat override), or by replacing this CLI with a
richer variant that reads per-chunk labels from a sidecar file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make ``src/`` importable so we can reuse the shared voice_pack types.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.voice_pack.dataset import export_dataset  # noqa: E402
from src.voice_pack.types import (  # noqa: E402
    EMOTION_CLASSES,
    DatasetManifest,
    TaggedChunk,
    VoiceChunk,
)


def _iter_transcripts(path: Path) -> list[VoiceChunk]:
    """Load every VoiceChunk from a transcripts.jsonl file.

    Each line is the ``to_dict()`` of a :class:`VoiceChunk`, which has the
    flat fields ``start / end / text / speaker / confidence``. We're
    strict about the schema: a malformed row raises rather than getting
    silently dropped, so the operator notices if the analyzer changed
    format.
    """
    chunks: list[VoiceChunk] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                chunk = VoiceChunk(
                    start=float(obj["start"]),
                    end=float(obj["end"]),
                    text=str(obj["text"]),
                    speaker=str(obj["speaker"]),
                    confidence=float(obj["confidence"]),
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"{path}:{line_no}: could not parse VoiceChunk row — {exc}"
                ) from exc
            chunks.append(chunk)
    return chunks


def export_for_speaker(
    *,
    transcripts_path: Path,
    source_audio_path: Path,
    speaker: str,
    out_dir: Path,
    emotion_label: str = "neutral",
    sample_rate_hz: int = 24000,
    rebalance_by_emotion: bool = False,
) -> DatasetManifest:
    """Run the whole bridge end-to-end and return the manifest.

    Kept as a plain function so it's unit-testable without invoking the
    argparse layer. The CLI entry point is :func:`main`.
    """
    if emotion_label not in EMOTION_CLASSES:
        raise ValueError(
            f"emotion label {emotion_label!r} not in {EMOTION_CLASSES}"
        )

    if not transcripts_path.exists():
        raise FileNotFoundError(f"transcripts file not found: {transcripts_path}")
    if not source_audio_path.exists():
        raise FileNotFoundError(f"source audio not found: {source_audio_path}")

    all_chunks = _iter_transcripts(transcripts_path)
    speaker_chunks = [c for c in all_chunks if c.speaker == speaker]
    if not speaker_chunks:
        seen = sorted({c.speaker for c in all_chunks})
        raise ValueError(
            f"no chunks for speaker {speaker!r} in {transcripts_path}. "
            f"Speakers present: {seen or '[none]'}"
        )

    tagged = [
        TaggedChunk.from_chunk(
            c, emotion=emotion_label, emotion_confidence=1.0
        )
        for c in speaker_chunks
    ]

    return export_dataset(
        tagged,
        source_audio_path=source_audio_path,
        out_dir=out_dir,
        sample_rate_hz=sample_rate_hz,
        rebalance_by_emotion=rebalance_by_emotion,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice_pack_export",
        description=(
            "Bridge stage between voice_pack_analyze and voice_pack_train. "
            "Reads transcripts.jsonl + source audio, filters to one speaker, "
            "labels every clip 'neutral' (override with --emotion-label), "
            "and writes a DatasetManifest ready for LoRA training."
        ),
    )
    parser.add_argument(
        "--transcripts",
        required=True,
        type=Path,
        help="Path to transcripts.jsonl (from voice_pack_analyze).",
    )
    parser.add_argument(
        "--source",
        required=True,
        type=Path,
        help="Original source audio file the transcripts were cut from.",
    )
    parser.add_argument(
        "--speaker",
        required=True,
        help=(
            "Diarization speaker id to export (e.g. SPEAKER_00). See "
            "speakers.yaml / report.md from the analyze stage."
        ),
    )
    parser.add_argument(
        "--out",
        "-o",
        required=True,
        type=Path,
        help=(
            "Output directory for the dataset (will contain wavs/, "
            "manifest.json, metadata.csv)."
        ),
    )
    parser.add_argument(
        "--emotion-label",
        default="neutral",
        choices=sorted(EMOTION_CLASSES),
        help=(
            "Emotion label to apply to every clip. Default: neutral. "
            "Use a dedicated emotion classifier upstream if per-clip labels "
            "are needed."
        ),
    )
    parser.add_argument(
        "--sample-rate-hz",
        type=int,
        default=24000,
        help="Target sample rate for exported clips. Default: 24000.",
    )
    parser.add_argument(
        "--rebalance-by-emotion",
        action="store_true",
        help=(
            "Upsample minority emotion classes so imbalanced datasets "
            "imprint minority prosody. Harmless (no-op) with a single "
            "emotion label."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        manifest = export_for_speaker(
            transcripts_path=args.transcripts,
            source_audio_path=args.source,
            speaker=args.speaker,
            out_dir=args.out,
            emotion_label=args.emotion_label,
            sample_rate_hz=args.sample_rate_hz,
            rebalance_by_emotion=args.rebalance_by_emotion,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    total_min = manifest.total_seconds / 60.0
    print(
        f"Exported {len(manifest.clips)} clips "
        f"({total_min:.1f} min) for speaker {manifest.speaker} "
        f"→ {args.out}/manifest.json"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
