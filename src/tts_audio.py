"""Audio combining helpers for TTS.

Extracted from ``src/tts_engine.py`` as part of the engine split. Trims
silence from each synthesized chunk and concatenates them via ffmpeg's
concat demuxer so that peak memory stays at a single chunk regardless
of book length (an 8 h audiobook used to allocate ~2.5 GB transiently
when pydub built the combined PCM buffer in RAM).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pydub import AudioSegment


def _trim_chunk_silence(
    segment: AudioSegment,
    threshold_db: float = -45.0,
    keep_ms: int = 30,
    trailing_threshold_db: float = -55.0,
    trailing_keep_ms: int = 100,
) -> AudioSegment:
    """Trim leading and trailing silence from a synthesized chunk.

    edge-tts returns each chunk with ~150ms leading and ~800ms trailing
    silence.  Without trimming, concatenating 100+ chunks produces ~1 second
    of dead air at every chunk boundary, which sounds like the voice is
    cutting mid-sentence.

    Trailing silence uses a **lower** (more negative) threshold than the
    leading edge so that quiet language-specific endings (e.g. Finnish
    unstressed word-final vowels) are not misclassified as silence and
    clipped.

    Args:
        segment: Audio segment to trim.
        threshold_db: Anything quieter than this is leading silence.
        keep_ms: Silence to keep after the leading trim (ms).
        trailing_threshold_db: Trailing-edge silence threshold (more negative
            = keep more of a quiet tail).
        trailing_keep_ms: Silence to keep before the trailing cut (ms).

    Returns:
        Trimmed audio segment.
    """
    from pydub.silence import detect_leading_silence

    lead = detect_leading_silence(segment, silence_threshold=threshold_db)
    trail = detect_leading_silence(
        segment.reverse(), silence_threshold=trailing_threshold_db
    )
    start = max(0, lead - keep_ms)
    end = len(segment) - max(0, trail - trailing_keep_ms)
    if end <= start:
        # Chunk was entirely silent — return it as-is to avoid a zero-length slice
        return segment
    return segment[start:end]


def _load_audio_with_retry(path: str, max_retries: int = 5, delay: float = 0.3) -> AudioSegment:
    """Load an audio file, retrying on Windows file-locking errors.

    edge-tts's async transports may hold file handles briefly after
    asyncio.run() returns. A short retry loop handles this gracefully.
    """
    import time
    for attempt in range(max_retries):
        try:
            return _trim_chunk_silence(AudioSegment.from_file(path))
        except PermissionError:
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                raise


def _concat_escape(path: Path) -> str:
    """Escape a filesystem path for the ffmpeg concat demuxer.

    The demuxer parses each line as ``file 'PATH'`` where single quotes
    delimit the string. A literal single quote inside the path must be
    written as ``'\\''`` (close quote, escaped literal, reopen). Forward
    slashes work on both POSIX and Windows.
    """
    return str(path).replace("\\", "/").replace("'", r"'\''")


def combine_audio_files(
    input_paths: list[str],
    output_path: str,
    inter_chunk_pause_ms: int = 200,
) -> None:
    """Combine chunk files into a single MP3 with bounded memory.

    Each input chunk is loaded one at a time, silence-trimmed with
    asymmetric thresholds (softer tail so Finnish word-final vowels are
    not clipped), normalised to a common ``(rate, channels, sample
    width)``, and written as WAV into a staging directory. A short
    inter-chunk silence is materialised once and reused via the concat
    list, avoiding N copies on disk. Final encode runs through ffmpeg's
    concat demuxer so the complete book is streamed directly from the
    staging WAVs to the output MP3 — Python only ever holds one chunk's
    PCM in memory.

    Args:
        input_paths: Ordered list of MP3/WAV file paths to concatenate.
        output_path: Destination MP3 path.
        inter_chunk_pause_ms: Length of the natural pause inserted
            between adjacent chunks (milliseconds). Zero disables the gap.

    Raises:
        ValueError: If ``input_paths`` is empty.
        FileNotFoundError: If the ffmpeg executable cannot be located.
        RuntimeError: If the ffmpeg concat step exits non-zero.
    """
    if not input_paths:
        raise ValueError("No audio files to combine.")

    # Ensure pydub + our subprocess call can both find ffmpeg.
    from src.ffmpeg_path import setup_ffmpeg_path, get_ffmpeg_exe
    setup_ffmpeg_path()
    ffmpeg_exe = get_ffmpeg_exe()
    if not ffmpeg_exe:
        raise FileNotFoundError(
            "ffmpeg executable not found; cannot assemble audio."
        )

    import shutil
    import tempfile

    staging_dir = Path(tempfile.mkdtemp(prefix="abm_concat_"))
    try:
        # Phase 1: trim each chunk and write to staging as WAV. We pick
        # the first chunk's stream parameters as the target for the whole
        # run so the concat demuxer (which requires matching streams)
        # sees a homogeneous input.
        target_rate: int | None = None
        target_channels: int | None = None
        target_sw: int | None = None
        trimmed_paths: list[Path] = []
        for i, path in enumerate(input_paths):
            segment = _load_audio_with_retry(path)
            if target_rate is None:
                target_rate = segment.frame_rate
                target_channels = segment.channels
                target_sw = segment.sample_width
            elif (
                segment.frame_rate != target_rate
                or segment.channels != target_channels
                or segment.sample_width != target_sw
            ):
                segment = (
                    segment.set_frame_rate(target_rate)
                    .set_channels(target_channels)
                    .set_sample_width(target_sw)
                )
            trimmed = staging_dir / f"chunk_{i:05d}.wav"
            segment.export(str(trimmed), format="wav")
            trimmed_paths.append(trimmed)

        # Phase 2: one reusable silence WAV referenced N-1 times in the
        # concat list — cheaper than writing it per gap.
        gap_path: Path | None = None
        if inter_chunk_pause_ms > 0 and len(trimmed_paths) > 1:
            gap_path = staging_dir / "gap.wav"
            gap_segment = (
                AudioSegment.silent(
                    duration=inter_chunk_pause_ms,
                    frame_rate=target_rate,
                )
                .set_channels(target_channels)
                .set_sample_width(target_sw)
            )
            gap_segment.export(str(gap_path), format="wav")

        # Phase 3: write the concat list.
        list_path = staging_dir / "concat.txt"
        with list_path.open("w", encoding="utf-8") as lf:
            for i, tp in enumerate(trimmed_paths):
                lf.write(f"file '{_concat_escape(tp)}'\n")
                if gap_path is not None and i < len(trimmed_paths) - 1:
                    lf.write(f"file '{_concat_escape(gap_path)}'\n")

        # Phase 4: stream-concat to the final MP3.
        cmd = [
            ffmpeg_exe,
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_path),
            "-c:a", "libmp3lame",
            "-b:a", "128k",
            str(output_path),
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            creationflags=creationflags,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or proc.stdout.strip() or "(no output)"
            raise RuntimeError(
                f"ffmpeg concat failed (exit {proc.returncode}): {stderr}"
            )
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
