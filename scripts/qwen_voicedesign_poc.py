#!/usr/bin/env python3
"""Standalone proof-of-concept for Qwen3-TTS VoiceDesign.

This is the Phase 1 GO / NO-GO gate from the build brief. It deliberately
lives OUTSIDE AudiobookMaker's engine architecture — it imports nothing from
``src/`` — so it proves the raw model works here before any integration is
built on top of it.

What it does
------------
1. Loads ``Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`` once.
2. Generates a few samples from different natural-language voice descriptions.
3. Writes them as ``.wav`` files you can listen to.
4. Prints generation time, real-time factor, and peak VRAM for each sample.

Usage
-----
    pip install -U qwen-tts
    python scripts/qwen_voicedesign_poc.py
    python scripts/qwen_voicedesign_poc.py --help

The model is Apache-2.0 and ~4 GB in bfloat16; it fits a 12 GB GPU comfortably.
Finnish is NOT one of the 10 supported languages — this POC defaults to English.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# The official VoiceDesign model id, confirmed from the Hugging Face model card.
DEFAULT_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"

# Three deliberately different descriptions so the listener can judge whether
# VoiceDesign produces genuinely distinct, usable narration voices.
DEFAULT_SAMPLES: list[tuple[str, str]] = [
    (
        "warm_male_narrator",
        "A warm male narrator in his mid-30s, calm and measured, with a "
        "friendly, reassuring tone suited to a non-fiction audiobook.",
    ),
    (
        "bright_female_narrator",
        "A bright, expressive young woman with a light, energetic voice, "
        "clear diction, and an engaging storytelling cadence.",
    ),
    (
        "deep_documentary_voice",
        "A deep, resonant documentary voice, slow and authoritative, with "
        "rich low tones and dramatic gravitas.",
    ),
]

# A short, neutral line of narration. Kept generic on purpose — no copyrighted
# source text (see CLAUDE.md). Override with --text.
DEFAULT_TEXT = (
    "The morning light spread slowly across the quiet valley, and for a "
    "moment everything was still."
)


def _repo_root() -> Path:
    """Return the repository root (the parent of this script's scripts/ dir)."""
    return Path(__file__).resolve().parent.parent


def _default_out_dir() -> Path:
    """Scratch output dir under .local/ (gitignored — see CLAUDE.md)."""
    return _repo_root() / ".local" / "scratch" / "qwen_voicedesign_poc"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="qwen_voicedesign_poc.py",
        description="Standalone POC for Qwen3-TTS VoiceDesign (Phase 1 gate).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--model-id",
        default=DEFAULT_MODEL_ID,
        help="Hugging Face model id for the VoiceDesign model.",
    )
    p.add_argument(
        "--text",
        default=DEFAULT_TEXT,
        help="The narration text to speak in every sample.",
    )
    p.add_argument(
        "--language",
        default="English",
        help=(
            "Language NAME as Qwen expects it (English, Chinese, Japanese, "
            "Korean, German, French, Russian, Portuguese, Spanish, Italian). "
            "Finnish is not supported."
        ),
    )
    p.add_argument(
        "--device",
        default="cuda:0",
        help="Device to load the model on.",
    )
    p.add_argument(
        "--attn",
        default="sdpa",
        choices=["sdpa", "flash_attention_2", "eager"],
        help=(
            "Attention implementation. 'sdpa' is built into PyTorch and needs "
            "nothing extra; 'flash_attention_2' is faster but needs flash-attn."
        ),
    )
    p.add_argument(
        "--out-dir",
        default=None,
        help="Where to write the .wav samples (default: .local/scratch/...).",
    )
    return p.parse_args(argv)


def _import_deps():
    """Import the heavy deps with friendly errors if they are missing."""
    try:
        import torch  # noqa: F401
    except ImportError:
        print(
            "ERROR: PyTorch is not installed. Install the model package first:\n"
            "    pip install -U qwen-tts",
            file=sys.stderr,
        )
        raise SystemExit(2)

    try:
        import soundfile  # noqa: F401
    except ImportError:
        print(
            "ERROR: soundfile is not installed (normally pulled in by qwen-tts):\n"
            "    pip install -U qwen-tts soundfile",
            file=sys.stderr,
        )
        raise SystemExit(2)

    try:
        from qwen_tts import Qwen3TTSModel  # noqa: F401
    except ImportError:
        print(
            "ERROR: the 'qwen-tts' package is not installed:\n"
            "    pip install -U qwen-tts",
            file=sys.stderr,
        )
        raise SystemExit(2)

    import torch
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel

    return torch, sf, Qwen3TTSModel


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    torch, sf, Qwen3TTSModel = _import_deps()

    if not torch.cuda.is_available():
        print(
            "ERROR: CUDA is not available. This POC needs an NVIDIA GPU.\n"
            "Check your driver / CUDA-enabled torch build.",
            file=sys.stderr,
        )
        return 2

    out_dir = Path(args.out_dir) if args.out_dir else _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    gpu_name = torch.cuda.get_device_name(0)
    print(f"GPU:        {gpu_name}")
    print(f"Model:      {args.model_id}")
    print(f"Attention:  {args.attn}")
    print(f"Output dir: {out_dir}")
    print("Loading model (first run downloads ~4 GB of weights)...")

    load_start = time.perf_counter()
    torch.cuda.reset_peak_memory_stats()
    model = Qwen3TTSModel.from_pretrained(
        args.model_id,
        device_map=args.device,
        dtype=torch.bfloat16,
        attn_implementation=args.attn,
    )
    load_secs = time.perf_counter() - load_start
    load_vram_gb = torch.cuda.max_memory_allocated() / (1024**3)
    print(f"Loaded in {load_secs:.1f}s. VRAM after load: {load_vram_gb:.2f} GB\n")

    peak_vram_gb = load_vram_gb
    for label, instruct in DEFAULT_SAMPLES:
        print(f"[{label}] {instruct}")
        torch.cuda.reset_peak_memory_stats()
        gen_start = time.perf_counter()
        wavs, sr = model.generate_voice_design(
            text=args.text,
            language=args.language,
            instruct=instruct,
        )
        gen_secs = time.perf_counter() - gen_start

        wav = wavs[0]
        audio_secs = len(wav) / float(sr)
        rtf = audio_secs / gen_secs if gen_secs > 0 else float("inf")
        sample_vram_gb = torch.cuda.max_memory_allocated() / (1024**3)
        peak_vram_gb = max(peak_vram_gb, sample_vram_gb)

        out_path = out_dir / f"{label}.wav"
        sf.write(str(out_path), wav, sr)
        print(
            f"  -> {out_path.name}: {audio_secs:.1f}s audio @ {sr} Hz, "
            f"generated in {gen_secs:.1f}s (RTF {rtf:.2f}x), "
            f"peak VRAM {sample_vram_gb:.2f} GB\n"
        )

    print(f"Done. {len(DEFAULT_SAMPLES)} samples in {out_dir}")
    print(f"Peak VRAM across the run: {peak_vram_gb:.2f} GB")
    print("Listen to the .wav files and decide GO / NO-GO before integration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
