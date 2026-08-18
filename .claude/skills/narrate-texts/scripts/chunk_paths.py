"""Locating cached chunk WAVs, whichever naming scheme wrote them."""
from __future__ import annotations


def chunk_wav_path(work, index, texts, language="fi", voice_key=""):
    """Path of the WAV for chunk ``index``, whatever the cache scheme.

    The runner addresses chunks by CONTENT: the filename is a hash of the
    chunk's text, language and voice, so the same words are reused wherever
    they move to. Building `ch01_chunk{i:04d}.wav` here silently found nothing
    and reported a perfectly good narration as unverifiable — which, since the
    transcript check is the completion gate, means the gate reads red for
    every run and an operator learns to ignore it.

    Falls back to the old index name so a cache written by an older build can
    still be verified.
    """
    import importlib.util
    from pathlib import Path

    work = Path(work)
    runner_path = (
        Path(__file__).resolve().parents[4] / "scripts"
        / "generate_chatterbox_audiobook.py"
    )
    try:
        spec = importlib.util.spec_from_file_location("_gca_keys", runner_path)
        gca = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gca)
        keys = gca._chunk_keys(list(texts), language, voice_key)
        candidate = gca._chunk_cache_path(work / ".chunks", keys[index])
        if candidate.exists():
            return candidate
    except Exception:
        pass
    return work / ".chunks" / f"ch01_chunk{index:04d}.wav"


def chunk_wavs(work):
    """Every cached chunk WAV, either naming scheme."""
    from pathlib import Path

    chunks = Path(work) / ".chunks"
    return sorted(chunks.glob("chunk_*.wav")) + sorted(
        chunks.glob("ch[0-9]*_chunk[0-9]*.wav")
    )
