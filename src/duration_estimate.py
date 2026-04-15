"""Estimate audio duration and synthesis wall-clock time for TTS jobs.

Pure Python, no GUI dependency. Used by the GUI to show rough ETAs
before a synthesis run starts. Refined live once synthesis begins
and real RTF measurements are available.

Rationale for the numbers
-------------------------
- Based on the v7 Finnish audiobook run and the Rubicon Route-B clip
  (723 chars -> 32 s audio == ~22 chars/s in English with Chatterbox).
- Chatterbox RTF 0.85 on RTX 3080 Ti comes from observed
  ``[routeB] chunk 1/3 14.2s synth 10.6s audio`` logs (synth/audio ratio
  ~1.3 -> effective RTF ~0.75, rounded to 0.85 for slightly pessimistic
  UX).
- Edge-TTS observed at around 2 minutes of wall time per hour of audio
  in the parallel generator; single-threaded GUI calls are slower, so
  we use RTF ~5.0 rather than the parallel ~10x.
- No attempt to self-calibrate yet; these are rough estimates that get
  refined by the live RTF stream once synthesis is actually running.
"""

from __future__ import annotations

from typing import Optional


# Characters per second of spoken audio, by language.
# Finnish is a bit slower than English because words are longer on
# average (agglutinative morphology) but syllables are short.
_CHARS_PER_SECOND_AUDIO = {
    "fi": 20.0,    # ~1200 chars/minute
    "en": 22.5,    # ~1350 chars/minute
}

# Real-time factor = audio_seconds / wall_seconds.
# Higher RTF means faster than realtime synthesis.
_ENGINE_RTF = {
    "edge": 5.0,            # cloud, single-thread GUI call
    "piper": 6.0,            # CPU, Finnish onnx model
    "chatterbox_fi": 0.85,   # RTX 3080 Ti baseline, slightly pessimistic
    "chatterbox": 0.85,      # alias
    "voxcpm": 1.0,           # GPU-dependent, rough
}

# Engines that need a GPU to be usable. If run on CPU we multiply the
# wall-time estimate by this penalty to flag the user.
_GPU_ENGINES = {"chatterbox_fi", "chatterbox", "voxcpm"}
_CPU_PENALTY = 20.0


def estimate_audio_duration(char_count: int, language: str = "fi") -> float:
    """Estimate spoken audio duration in seconds for ``char_count`` characters.

    Unknown language falls back to the Finnish rate.
    """
    if char_count <= 0:
        return 0.0
    rate = _CHARS_PER_SECOND_AUDIO.get(language, _CHARS_PER_SECOND_AUDIO["fi"])
    return char_count / rate


def estimate_wall_time(audio_seconds: float, engine_id: str, device: str = "cuda") -> float:
    """Estimate synthesis wall-clock time in seconds.

    Unknown engine returns ``audio_seconds * 2`` as a safe conservative
    estimate. If ``device='cpu'`` and the engine normally wants a GPU,
    the wall time is multiplied by 20 to flag that the run will be
    painfully slow.
    """
    if audio_seconds <= 0:
        return 0.0
    rtf = _ENGINE_RTF.get(engine_id)
    if rtf is None:
        return audio_seconds * 2.0
    wall = audio_seconds / rtf
    if device == "cpu" and engine_id in _GPU_ENGINES:
        wall *= _CPU_PENALTY
    return wall


def format_duration(seconds: Optional[float]) -> str:
    """Format a duration as a short human-readable string.

    - ``< 60 s``    -> ``'45 s'``
    - ``< 3600 s``  -> ``'12 min 34 s'``
    - ``>= 3600 s`` -> ``'1 h 23 min'``
    - ``0``, negative -> ``'0 s'``
    - ``None`` -> ``'?'``
    """
    if seconds is None:
        return "?"
    if seconds <= 0:
        return "0 s"
    if seconds < 60:
        return f"{int(round(seconds))} s"
    if seconds < 3600:
        minutes = int(seconds // 60)
        rem = int(round(seconds - minutes * 60))
        if rem == 60:
            minutes += 1
            rem = 0
        return f"{minutes} min {rem} s"
    hours = int(seconds // 3600)
    rem_min = int(round((seconds - hours * 3600) / 60))
    if rem_min == 60:
        hours += 1
        rem_min = 0
    return f"{hours} h {rem_min} min"


def estimate_job(
    char_count: int,
    engine_id: str,
    language: str,
    device: str = "cuda",
) -> dict:
    """Estimate audio + wall time for a job and return a summary dict.

    Keys:
      - ``audio_seconds``: estimated spoken audio length
      - ``wall_seconds``: estimated synthesis wall time
      - ``chars_per_second_synth``: effective synthesis throughput
      - ``audio_human``: formatted audio duration
      - ``wall_human``: formatted wall time
    """
    audio = estimate_audio_duration(char_count, language)
    wall = estimate_wall_time(audio, engine_id, device)
    cps_synth = (char_count / wall) if wall > 0 else 0.0
    return {
        "audio_seconds": audio,
        "wall_seconds": wall,
        "chars_per_second_synth": cps_synth,
        "audio_human": format_duration(audio),
        "wall_human": format_duration(wall),
    }
