"""Audio combining helpers for TTS.

Extracted from ``src/tts_engine.py`` as part of the engine split. Trims
silence from each synthesized chunk and concatenates them with pydub.
"""

from __future__ import annotations

from pydub import AudioSegment


def _trim_chunk_silence(
    segment: AudioSegment,
    threshold_db: float = -45.0,
    keep_ms: int = 30,
) -> AudioSegment:
    """Trim leading and trailing silence from a synthesized chunk.

    edge-tts returns each chunk with ~150ms leading and ~800ms trailing
    silence.  Without trimming, concatenating 100+ chunks produces ~1 second
    of dead air at every chunk boundary, which sounds like the voice is
    cutting mid-sentence.

    Args:
        segment: Audio segment to trim.
        threshold_db: Anything quieter than this is considered silence.
        keep_ms: Amount of silence to keep at each edge for a natural edge.

    Returns:
        Trimmed audio segment.
    """
    from pydub.silence import detect_leading_silence

    lead = detect_leading_silence(segment, silence_threshold=threshold_db)
    trail = detect_leading_silence(segment.reverse(), silence_threshold=threshold_db)
    start = max(0, lead - keep_ms)
    end = len(segment) - max(0, trail - keep_ms)
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


def combine_audio_files(
    input_paths: list[str],
    output_path: str,
    inter_chunk_pause_ms: int = 200,
) -> None:
    """Combine multiple MP3 files into one using pydub.

    Each chunk is trimmed of leading/trailing silence before concatenation so
    that the seams between chunks don't sound like dead air.  A short natural
    pause is inserted between chunks so the speech flow still feels paced.

    Args:
        input_paths: Ordered list of MP3 file paths to concatenate.
        output_path: Destination MP3 path.
        inter_chunk_pause_ms: Length of the synthetic pause inserted between
            adjacent chunks (milliseconds).

    Raises:
        ValueError: If input_paths is empty.
    """
    if not input_paths:
        raise ValueError("No audio files to combine.")

    # Ensure pydub can find ffmpeg (safety net in case setup_ffmpeg_path()
    # was not called or was called before pydub was imported).
    from src.ffmpeg_path import setup_ffmpeg_path
    setup_ffmpeg_path()

    gap = AudioSegment.silent(duration=inter_chunk_pause_ms)
    combined = AudioSegment.empty()
    for i, path in enumerate(input_paths):
        # Auto-detect format from the file extension so both edge-tts MP3
        # chunks and piper WAV chunks are supported.
        segment = _load_audio_with_retry(path)
        combined += segment
        if i < len(input_paths) - 1:
            combined += gap

    combined.export(output_path, format="mp3")
