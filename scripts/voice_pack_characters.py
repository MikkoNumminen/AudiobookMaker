"""Voice pack character-clustering CLI — optional stage between analyze and export.

After :mod:`scripts.voice_pack_analyze` produces ``transcripts.jsonl``,
the chunks are tagged with a speaker id but not a character id. For
novel audiobooks where one reader performs many characters that's the
wrong granularity: a LoRA trained on "all the man's chunks" ends up
as an averaged blend of narrator + villain + hero, not a clean voice.

This stage subclusters each speaker's chunks acoustically to discover
distinct character voices and emits
``transcripts_with_characters.jsonl`` with per-chunk ``character`` tags.
:mod:`scripts.voice_pack_export` then accepts ``--character CHAR_A`` to
build a per-character dataset.

Dependencies:
- NumPy (already in the .venv-chatterbox env — imported lazily).
- An embedder that turns an audio slice into a vector. The default
  embedder is Chatterbox's voice encoder (``engine.ve.embeds_from_wavs``)
  which needs torch + chatterbox; tests inject a fake embedder.
- An audio slicer that returns a 16 kHz mono waveform. Defaults to a
  pydub-based slicer; tests inject a fake slicer.

The run is O(N²) in embedding space per speaker. For a 1 h source with
two speakers of ~500 chunks each that's a few seconds of CPU after
embeddings are computed; the embedding step itself is the tall pole.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from collections.abc import Callable
from pathlib import Path

# Make ``src/`` importable when running this script directly.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.voice_pack.characters import (  # noqa: E402
    CharacterClusteringResult,
    ClusterConfig,
    cluster_all_speakers,
)
from src.voice_pack.types import VoiceChunk  # noqa: E402

# Type aliases for the injection seams. Kept at module scope so tests
# can reference them from ``voice_pack_characters.AudioSlicer`` etc.
# The slicer returns ``(waveform_mono, sample_rate_hz)``; the embedder
# turns that pair into a 1-D vector.
AudioSlicer = Callable[[Path, float, float], tuple[object, int]]
Embedder = Callable[[object, int], object]


def _iter_transcripts(path: Path) -> list[VoiceChunk]:
    """Load every VoiceChunk from a transcripts.jsonl file.

    Mirrors :mod:`scripts.voice_pack_export` but is duplicated here to
    keep this CLI independent (scripts/ is not a package). The
    ``character`` field is optional and preserved when present so the
    stage can be re-run to refine clusters.
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
                chunk = VoiceChunk(
                    start=float(obj["start"]),
                    end=float(obj["end"]),
                    text=str(obj["text"]),
                    speaker=str(obj["speaker"]),
                    confidence=float(obj["confidence"]),
                    character=str(character) if character is not None else None,
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"{path}:{line_no}: could not parse VoiceChunk row — {exc}"
                ) from exc
            chunks.append(chunk)
    return chunks


def _default_slicer() -> AudioSlicer:
    """Return the production pydub slicer.

    Lazy-imports pydub (and its ffmpeg discovery) so tests that inject
    a fake slicer don't need pydub installed.
    """

    def _slice(src: Path, start_s: float, end_s: float) -> tuple[object, int]:
        import numpy as np  # type: ignore
        from pydub import AudioSegment  # type: ignore[import-not-found]

        audio = AudioSegment.from_file(str(src))
        clip = audio[int(start_s * 1000) : int(end_s * 1000)]
        clip = clip.set_channels(1).set_frame_rate(16000)
        samples = np.array(clip.get_array_of_samples(), dtype=np.float32)
        # Normalise int16 PCM to [-1.0, 1.0] float, the shape the
        # Chatterbox voice encoder expects.
        if clip.sample_width == 2:
            samples = samples / 32768.0
        return samples, 16000

    return _slice


def _default_embedder() -> Embedder:
    """Return the production Chatterbox voice encoder embedder.

    Lazy-imports torch and chatterbox. The model loads once and every
    subsequent call reuses it — hence the module-level cache.
    """
    cache: dict[str, object] = {}

    def _embed(wav: object, sample_rate_hz: int) -> object:
        if "engine" not in cache:
            import torch  # type: ignore
            from chatterbox.mtl_tts import (  # type: ignore[import-not-found]
                ChatterboxMultilingualTTS,
            )

            device = "cuda" if torch.cuda.is_available() else "cpu"
            cache["engine"] = ChatterboxMultilingualTTS.from_pretrained(device)
        engine = cache["engine"]
        # engine.ve returns shape (B, D); we feed one wav at a time.
        emb = engine.ve.embeds_from_wavs([wav], sample_rate=sample_rate_hz)  # type: ignore[attr-defined]
        # emb may be torch tensor or numpy; normalise to a flat numpy row.
        try:
            import numpy as np  # type: ignore

            if hasattr(emb, "detach"):
                emb = emb.detach().cpu().numpy()
            arr = np.asarray(emb).reshape(-1)
            return arr
        except Exception:  # pragma: no cover — belt-and-suspenders
            return emb

    return _embed


def cluster_transcripts(
    *,
    transcripts_path: Path,
    source_audio_path: Path,
    out_dir: Path,
    distance_threshold: float = 0.25,
    min_character_seconds: float = 60.0,
    min_character_chunks: int = 8,
    max_characters_per_speaker: int | None = None,
    max_chunks_per_speaker: int | None = None,
    audio_slicer: AudioSlicer | None = None,
    embedder: Embedder | None = None,
    verbose: bool = False,
) -> CharacterClusteringResult:
    """Run the character-clustering stage end-to-end.

    Writes three artefacts to ``out_dir``:

    * ``transcripts_with_characters.jsonl`` — one chunk per line with
      ``character`` populated.
    * ``characters.yaml`` — aggregate per-character stats, biggest
      first.
    * ``characters_report.md`` — human-readable summary.

    ``max_chunks_per_speaker`` subsamples chunks for the embedding step
    (useful if a speaker has thousands of chunks and embedding is the
    slow path). Chunks not sampled are left with their existing
    ``character`` value (usually ``None``); they don't get a character
    label and will be invisible to ``voice_pack_export --character``.

    The slicer/embedder seams are there for tests and for swapping the
    embedding model; default production settings pick the pydub slicer
    and the Chatterbox voice encoder.
    """
    if not transcripts_path.exists():
        raise FileNotFoundError(f"transcripts file not found: {transcripts_path}")
    if not source_audio_path.exists():
        raise FileNotFoundError(f"source audio not found: {source_audio_path}")

    slicer: AudioSlicer = audio_slicer or _default_slicer()
    embed: Embedder = embedder or _default_embedder()

    all_chunks = _iter_transcripts(transcripts_path)

    # Decide which chunk indices get embedded. For each speaker we keep
    # up to ``max_chunks_per_speaker`` chunks, evenly spread in time so
    # a biased narrative tail doesn't dominate. When the cap is unset
    # (or speaker count is under the cap), we embed every chunk.
    indices_to_embed: list[int] = []
    from collections import defaultdict

    by_speaker_indices: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(all_chunks):
        by_speaker_indices[c.speaker].append(i)

    for speaker, indices in by_speaker_indices.items():
        if max_chunks_per_speaker is None or len(indices) <= max_chunks_per_speaker:
            indices_to_embed.extend(indices)
            continue
        # Even-stride subsample preserving the first and last chunk.
        step = len(indices) / float(max_chunks_per_speaker)
        picked = sorted({indices[int(k * step)] for k in range(max_chunks_per_speaker)})
        indices_to_embed.extend(picked)

    indices_to_embed.sort()

    if verbose:
        print(
            f"[voice_pack_characters] embedding {len(indices_to_embed)} of "
            f"{len(all_chunks)} chunks across {len(by_speaker_indices)} speakers"
        )

    # Embed each selected chunk.
    embeddings_by_index: dict[int, object] = {}
    for n, idx in enumerate(indices_to_embed, start=1):
        chunk = all_chunks[idx]
        wav, sr = slicer(source_audio_path, chunk.start, chunk.end)
        vec = embed(wav, sr)
        embeddings_by_index[idx] = vec
        if verbose and (n % 50 == 0 or n == len(indices_to_embed)):
            print(
                f"[voice_pack_characters] embedded {n}/{len(indices_to_embed)}"
            )

    config = ClusterConfig(
        distance_threshold=distance_threshold,
        min_character_seconds=min_character_seconds,
        min_character_chunks=min_character_chunks,
        max_characters_per_speaker=max_characters_per_speaker,
    )
    result = cluster_all_speakers(all_chunks, embeddings_by_index, config=config)

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "transcripts_with_characters.jsonl", result.chunks)
    _write_characters_yaml(out_dir / "characters.yaml", result)
    _write_characters_report(
        out_dir / "characters_report.md",
        result=result,
        transcripts_path=transcripts_path,
        source_audio_path=source_audio_path,
        config=config,
        n_embedded=len(indices_to_embed),
        n_total=len(all_chunks),
    )
    return result


def _write_jsonl(path: Path, chunks: list[VoiceChunk]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for chunk in chunks:
            fh.write(json.dumps(chunk.to_dict(), ensure_ascii=False))
            fh.write("\n")


def _write_characters_yaml(path: Path, result: CharacterClusteringResult) -> None:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - trivial guard
        raise RuntimeError(
            "PyYAML is required for voice_pack_characters. Install with: pip install pyyaml"
        ) from exc
    payload = [s.to_dict() for s in result.summaries]
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)


def _write_characters_report(
    path: Path,
    *,
    result: CharacterClusteringResult,
    transcripts_path: Path,
    source_audio_path: Path,
    config: ClusterConfig,
    n_embedded: int,
    n_total: int,
) -> None:
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    lines: list[str] = []
    lines.append("# Voice pack character clustering")
    lines.append("")
    lines.append(f"Generated {now}.")
    lines.append("")
    lines.append("## Source")
    lines.append("")
    lines.append(f"- Transcripts: `{transcripts_path}`")
    lines.append(f"- Source audio: `{source_audio_path}`")
    lines.append(f"- Chunks embedded: {n_embedded} of {n_total}")
    lines.append("")
    lines.append("## Parameters")
    lines.append("")
    lines.append(f"- distance_threshold: {config.distance_threshold}")
    lines.append(f"- min_character_seconds: {config.min_character_seconds}")
    lines.append(f"- min_character_chunks: {config.min_character_chunks}")
    lines.append("")
    lines.append("## Characters")
    lines.append("")
    lines.append(
        "| Speaker | Character | Total minutes | Chunks | Mean chunk (s) |"
    )
    lines.append("|---------|-----------|---------------|--------|----------------|")
    for s in result.summaries:
        minutes = s.total_seconds / 60.0
        lines.append(
            f"| {s.speaker} | {s.character} | {minutes:.1f} | "
            f"{s.chunk_count} | {s.mean_chunk_seconds:.2f} |"
        )
    if not result.summaries:
        lines.append("| _(no characters)_ |  |  |  |  |")
    lines.append("")
    lines.append("## Next steps")
    lines.append("")
    lines.append(
        "Pick a `speaker` + `character` pair from the table above and run:"
    )
    lines.append("")
    lines.append("```")
    lines.append(
        ".venv-chatterbox/Scripts/python.exe scripts/voice_pack_export.py \\"
    )
    lines.append(
        "  --transcripts transcripts_with_characters.jsonl \\"
    )
    lines.append(
        f"  --source {source_audio_path} \\"
    )
    lines.append("  --speaker SPEAKER_00 \\")
    lines.append("  --character CHAR_A \\")
    lines.append("  --out dataset_char_a/")
    lines.append("```")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice_pack_characters",
        description=(
            "Cluster a voice pack transcripts.jsonl by acoustic similarity "
            "within each speaker to discover distinct character voices. "
            "Writes transcripts_with_characters.jsonl + characters.yaml + "
            "characters_report.md. Feed the new jsonl into "
            "voice_pack_export.py --character CHAR_A to produce a per-"
            "character training dataset."
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
        "--out",
        "-o",
        required=True,
        type=Path,
        help=(
            "Output directory for transcripts_with_characters.jsonl, "
            "characters.yaml, characters_report.md."
        ),
    )
    parser.add_argument(
        "--distance-threshold",
        type=float,
        default=0.25,
        help=(
            "Cosine distance cutoff. Lower = more, tighter clusters; "
            "higher = fewer, broader clusters. Default 0.25."
        ),
    )
    parser.add_argument(
        "--min-character-seconds",
        type=float,
        default=60.0,
        help=(
            "Clusters with less total audio than this (in seconds) are "
            "folded into the dominant (narrator) cluster. Default 60."
        ),
    )
    parser.add_argument(
        "--min-character-chunks",
        type=int,
        default=8,
        help=(
            "Clusters with fewer chunks than this are folded into the "
            "dominant cluster. Default 8."
        ),
    )
    parser.add_argument(
        "--max-characters-per-speaker",
        type=int,
        default=None,
        help=(
            "Cap the number of distinct characters kept per reader, "
            "ranked by total duration. Smaller clusters fold into the "
            "dominant one. Use this to budget GPU time: each surviving "
            "character gets its own LoRA adapter. Typical 3-5 for a "
            "main-cast voice pack; leave unset for exhaustive coverage."
        ),
    )
    parser.add_argument(
        "--max-chunks-per-speaker",
        type=int,
        default=None,
        help=(
            "Optional cap on how many chunks per speaker get embedded, "
            "evenly spread across the timeline. Saves GPU time on very "
            "long source files at the cost of clustering resolution."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print per-stage progress to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        result = cluster_transcripts(
            transcripts_path=args.transcripts,
            source_audio_path=args.source,
            out_dir=args.out,
            distance_threshold=args.distance_threshold,
            min_character_seconds=args.min_character_seconds,
            min_character_chunks=args.min_character_chunks,
            max_characters_per_speaker=args.max_characters_per_speaker,
            max_chunks_per_speaker=args.max_chunks_per_speaker,
            verbose=args.verbose,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Clustered {len(result.chunks)} chunks into "
        f"{len(result.summaries)} (speaker, character) buckets "
        f"→ {args.out}/transcripts_with_characters.jsonl"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
