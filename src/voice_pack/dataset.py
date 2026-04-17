"""Per-speaker training dataset export for the voice-pack pipeline.

Takes a list of :class:`TaggedChunk` objects that all belong to one speaker,
slices the source audio into per-chunk WAV files, and writes an LJSpeech-style
layout on disk::

    <root_dir>/
      wavs/
        0000.wav
        0001.wav
        ...
      metadata.csv   # 'nnnn|text|emotion|duration' rows, pipe-delimited
      manifest.json  # full DatasetManifest serialised

The exporter is deliberately I/O-thin: audio cutting is delegated to a
``audio_slicer`` callable so tests can run without ffmpeg / pydub installed.
The default slicer is a lazy pydub wrapper.

Rebalancing is optional. When training LoRA voice clones, the dominant
calm-narration class tends to drown out minority classes (shouts, anger,
grief). :func:`rebalance_chunks` upsamples under-represented emotions so the
model actually learns them.
"""

from __future__ import annotations

import collections
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from src.voice_pack.types import DatasetClip, DatasetManifest, TaggedChunk

# Type alias for the audio slicer callable. Kept explicit so callers can
# drop in a fake slicer in tests without needing ffmpeg / pydub on the box.
AudioSlicer = Callable[[Path, float, float, Path, int], None]


def _pydub_slicer(
    src: Path,
    start_s: float,
    end_s: float,
    out_path: Path,
    target_sr: int,
) -> None:
    """Default audio slicer. Lazy-imports pydub so tests don't need it.

    Writes a mono WAV at ``target_sr`` Hz covering ``[start_s, end_s)`` of
    the source file.
    """
    # Imported here on purpose: pydub pulls in audioop / ffmpeg discovery
    # and we do not want that cost on module import or in unit tests.
    from pydub import AudioSegment  # type: ignore[import-not-found]

    audio = AudioSegment.from_file(str(src))
    clip = audio[int(start_s * 1000) : int(end_s * 1000)]
    clip = clip.set_channels(1).set_frame_rate(target_sr)
    clip.export(str(out_path), format="wav")


def _assert_single_speaker(chunks: list[TaggedChunk]) -> None:
    """Raise ValueError if chunks span more than one speaker."""
    speakers = {c.speaker for c in chunks}
    if len(speakers) > 1:
        speaker_list = sorted(speakers)
        raise ValueError(
            f"export_dataset expects all chunks to belong to one speaker; "
            f"got {len(speaker_list)}: {speaker_list}"
        )


def rebalance_chunks(
    chunks: list[TaggedChunk],
    *,
    target_per_emotion: Optional[int] = None,
    random_seed: Optional[int] = 42,
) -> list[TaggedChunk]:
    """Rebalance chunks by emotion so minority classes aren't drowned out.

    Behaviour:

    * If ``chunks`` is empty, return ``[]``.
    * Group chunks by ``.emotion``. Determine the target count: either the
      ``target_per_emotion`` argument, or the size of the largest existing
      class if that argument is ``None``.
    * For each emotion class:

      * If the class has fewer than ``target`` chunks, sample with
        replacement until it hits ``target`` (upsample).
      * If the class has more than ``target`` chunks, sample *without*
        replacement down to ``target`` (downsample).
      * If equal, keep as-is.

    * Combine all classes, shuffle with ``random.Random(random_seed)`` for
      determinism, and return the new list.

    The output is a new list; the input is not mutated. Order is not
    preserved (the result is shuffled).
    """
    if not chunks:
        return []

    rng = random.Random(random_seed)

    groups: dict[str, list[TaggedChunk]] = collections.defaultdict(list)
    for chunk in chunks:
        groups[chunk.emotion].append(chunk)

    if target_per_emotion is None:
        target = max(len(v) for v in groups.values())
    else:
        target = target_per_emotion

    out: list[TaggedChunk] = []
    # Iterate in a deterministic key order so random draws are reproducible.
    for emotion in sorted(groups):
        bucket = groups[emotion]
        if len(bucket) == target:
            out.extend(bucket)
        elif len(bucket) < target:
            # Upsample with replacement.
            out.extend(bucket)
            extra_needed = target - len(bucket)
            out.extend(rng.choices(bucket, k=extra_needed))
        else:
            # Downsample without replacement.
            out.extend(rng.sample(bucket, k=target))

    rng.shuffle(out)
    return out


def export_dataset(
    chunks: list[TaggedChunk],
    source_audio_path: str | Path,
    out_dir: str | Path,
    *,
    sample_rate_hz: int = 24000,
    rebalance_by_emotion: bool = False,
    target_per_emotion: Optional[int] = None,
    audio_slicer: Optional[AudioSlicer] = None,
    random_seed: Optional[int] = 42,
) -> DatasetManifest:
    """Export per-chunk WAV clips + manifest for one speaker's training set.

    Parameters
    ----------
    chunks:
        Tagged chunks for a single speaker. Raises ``ValueError`` if the
        chunks span more than one speaker.
    source_audio_path:
        Path to the original (full) audio file the chunks were cut from.
    out_dir:
        Destination directory. Created if absent, along with ``out_dir/wavs``.
        Existing files in this directory are overwritten.
    sample_rate_hz:
        Target sample rate for the exported WAVs. Stored in the manifest.
    rebalance_by_emotion:
        If True, run :func:`rebalance_chunks` on the input before slicing.
    target_per_emotion:
        Forwarded to :func:`rebalance_chunks` when rebalancing.
    audio_slicer:
        Callable ``(src_path, start_s, end_s, out_path, target_sr) -> None``
        that writes a single clip. Defaults to the pydub-based slicer.
    random_seed:
        Seed for the rebalance RNG.

    Returns
    -------
    DatasetManifest
        In-memory manifest. Also written to ``out_dir/manifest.json`` and
        with a companion ``out_dir/metadata.csv`` in LJSpeech format.
    """
    _assert_single_speaker(chunks)

    if rebalance_by_emotion and chunks:
        chunks = rebalance_chunks(
            chunks,
            target_per_emotion=target_per_emotion,
            random_seed=random_seed,
        )

    slicer: AudioSlicer = audio_slicer if audio_slicer is not None else _pydub_slicer

    src_path = Path(source_audio_path)
    root = Path(out_dir)
    wavs_dir = root / "wavs"
    root.mkdir(parents=True, exist_ok=True)
    wavs_dir.mkdir(parents=True, exist_ok=True)

    # Derive the speaker label from the chunks. Empty input means we fall back
    # to an empty string; callers passing empty chunks are usually testing or
    # staging the output directory, not producing a real dataset.
    speaker = chunks[0].speaker if chunks else ""

    clips: list[DatasetClip] = []
    total_seconds = 0.0
    emotion_counter: collections.Counter[str] = collections.Counter()

    metadata_rows: list[str] = []

    for index, chunk in enumerate(chunks):
        filename = f"{index:04d}.wav"
        rel_path = f"wavs/{filename}"
        out_path = wavs_dir / filename

        slicer(src_path, chunk.start, chunk.end, out_path, sample_rate_hz)

        duration = chunk.duration
        clip = DatasetClip(
            path=rel_path,
            text=chunk.text,
            emotion=chunk.emotion,
            speaker=chunk.speaker,
            duration=duration,
        )
        clips.append(clip)
        total_seconds += duration
        emotion_counter[chunk.emotion] += 1

        path_noext = rel_path[: -len(".wav")]
        metadata_rows.append(
            f"{path_noext}|{chunk.text}|{chunk.emotion}|{duration:.3f}"
        )

    manifest = DatasetManifest(
        speaker=speaker,
        root_dir=root,
        clips=clips,
        total_seconds=total_seconds,
        emotion_counts=dict(emotion_counter),
        sample_rate_hz=sample_rate_hz,
    )

    _write_manifest(manifest, root)
    _write_metadata_csv(metadata_rows, root)

    return manifest


def _write_manifest(manifest: DatasetManifest, root: Path) -> None:
    """Write the manifest as ``manifest.json`` under ``root``, UTF-8."""
    manifest_path = root / "manifest.json"
    payload = json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False)
    manifest_path.write_text(payload, encoding="utf-8")


def _write_metadata_csv(rows: list[str], root: Path) -> None:
    """Write LJSpeech-style ``metadata.csv``: one row per line, UTF-8, no header."""
    csv_path = root / "metadata.csv"
    # newline="" keeps our explicit '\n' joins from being mangled on Windows.
    body = "\n".join(rows)
    # Trailing newline for POSIX-friendliness when rows are present; empty file
    # when there are no rows so downstream tools get a clean empty CSV.
    if rows:
        body += "\n"
    csv_path.write_text(body, encoding="utf-8", newline="")
