#!/usr/bin/env python
"""dev_qwen_tts.py — experimental Qwen3-TTS runner for macOS Apple Silicon.

Developer-only tool for testing Qwen3-TTS on a Mac without CUDA. Not part
of the shipped AudiobookMaker app. Run it from the repository root, inside
a venv that has the needed extras installed (see INSTALLATION below).

Usage:
    python dev_qwen_tts.py book.pdf
    python dev_qwen_tts.py book.pdf --voice-description "tired Finnish librarian, monotone"
    python dev_qwen_tts.py book.pdf --ref-audio my_voice.wav --language English
    python dev_qwen_tts.py book.pdf --voice-description "aggressive deep male, slurred speech"

Status: EXPERIMENTAL. Qwen3-TTS is officially CUDA-only and relies on
flash-attn3 kernels. This script asks the model to run on MPS with an
SDPA attention fallback. Several things may not work:

- The model may refuse to load at all on MPS
- Generation may be very slow or hang
- **Finnish is NOT in the official supported-language list.** The model
  cards for all three variants only list: Chinese, English, Japanese,
  Korean, German, French, Russian, Portuguese, Spanish, Italian (10
  languages). Passing --language Finnish will probably produce garbage
  or silence. If you want Finnish, use the Edge-TTS / Piper engines in
  the main GUI instead.
- float16 on MPS has its own bug history; voice-design mode may need
  dtype=torch.float32 as a fallback

Use this script to gauge feasibility on English, not to produce final
Finnish audiobooks.

INSTALLATION:
    # CRITICAL: qwen_tts uses Python 3.10+ syntax (`str | None`) in its
    # source, so it will NOT run under the main project venv (Python 3.9).
    # Create a dedicated venv with Python 3.11+ and install into it:
    #
    #   brew install python@3.11 sox
    #   python3.11 -m venv .venv-qwen
    #   .venv-qwen/bin/pip install \
    #       torch torchaudio transformers==4.57.3 accelerate \
    #       einops librosa sox soundfile onnxruntime \
    #       huggingface_hub PyMuPDF
    #
    # Then run the script via the dedicated venv:
    #
    #   .venv-qwen/bin/python dev_qwen_tts.py book.pdf --max-chunks 4
    #
    # The qwen_tts Python module itself is NOT on PyPI. It is vendored
    # inside the official HuggingFace Space. This script downloads the
    # Space on first run and adds it to sys.path so `import qwen_tts`
    # works. The `sox` system binary (not just the Python wrapper) is
    # also required — install via `brew install sox` on macOS.

Models downloaded on first run (~6.5 GB total across all three):
    Qwen/Qwen3-TTS-12Hz-0.6B-Base           # voice cloning
    Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice    # preset voices
    Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign    # natural-language voice design
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# HuggingFace model IDs for the three Qwen3-TTS variants.
MODEL_BASE = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
MODEL_CUSTOM = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
MODEL_VOICEDESIGN = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"

# HuggingFace Space that vendors the qwen_tts Python module.
QWEN_TTS_SPACE = "Qwen/Qwen3-TTS"

# Qwen3-TTS chunks perform badly on long context. 500 chars at sentence
# boundaries is a safe middle ground between throughput and reliability.
MAX_CHUNK_CHARS = 500

# Default max_new_tokens guess for generation. The official Space uses
# 2048. Adjust here if the model hangs on specific inputs.
MAX_NEW_TOKENS = 2048

# Official preset speakers baked into the CustomVoice model (from the
# model card). Exact capitalization matters — the Space passes these
# strings through verbatim. Default to "Vivian" because she's listed
# first on the card.
PRESET_SPEAKERS = (
    "Vivian",
    "Serena",
    "Uncle_Fu",
    "Dylan",
    "Eric",
    "Ryan",
    "Aiden",
    "Ono_Anna",
    "Sohee",
)
DEFAULT_PRESET_SPEAKER = "Vivian"

# Languages officially supported by all three Qwen3-TTS variants. The
# model's runtime check is case-sensitive lowercase; "auto" lets the
# model try to detect the language from the text (and is the only hope
# for Finnish input, though quality is unpredictable).
SUPPORTED_LANGUAGES = {
    "auto",
    "chinese",
    "english",
    "french",
    "german",
    "italian",
    "japanese",
    "korean",
    "portuguese",
    "russian",
    "spanish",
}


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "pdf",
        type=str,
        help="Path to the input PDF file.",
    )
    parser.add_argument(
        "--voice-description",
        type=str,
        default=None,
        help=(
            "Natural-language voice description. "
            "Triggers the 1.7B VoiceDesign model. "
            'Example: --voice-description "tired Finnish librarian, monotone"'
        ),
    )
    parser.add_argument(
        "--ref-audio",
        type=str,
        default=None,
        help=(
            "Path to a reference WAV file for voice cloning. "
            "Triggers the 0.6B Base model with generate_voice_clone(). "
            "If used together with --voice-description, --ref-audio wins."
        ),
    )
    parser.add_argument(
        "--language",
        type=str,
        default="auto",
        help=(
            "Language of the input text (default: auto-detect). "
            "Qwen3-TTS's runtime check accepts only these lowercase values: "
            "auto, chinese, english, french, german, italian, japanese, "
            "korean, portuguese, russian, spanish. Finnish is NOT in the "
            "list and passing it raises ValueError at generate time — use "
            "'auto' to let the model try to detect the language, or "
            "'english' to hear the text read phonetically in English. "
            "Values are lowercased before being sent to the model."
        ),
    )
    parser.add_argument(
        "--speaker",
        type=str,
        default=DEFAULT_PRESET_SPEAKER,
        choices=PRESET_SPEAKERS,
        help=(
            f"Preset speaker name for CustomVoice mode "
            f"(ignored when --ref-audio or --voice-description is used). "
            f"Default: {DEFAULT_PRESET_SPEAKER}."
        ),
    )
    parser.add_argument(
        "--device",
        type=str,
        default="mps",
        choices=("mps", "cpu", "cuda"),
        help=(
            "Compute device. Default: mps (Apple Silicon GPU). Fall back "
            "to 'cpu' if MPS fails — Qwen3-TTS's codebook head violates "
            "MPS's 65536-output-channel limit, so MPS generation is "
            "known to crash with NotImplementedError on many layers. "
            "CPU is slow (minutes per chunk) but reliable."
        ),
    )
    parser.add_argument(
        "--max-chunks",
        type=int,
        default=0,
        help=(
            "If >0, stop after this many chunks. Useful for a quick quality "
            "check before committing to a full book run."
        ),
    )
    return parser.parse_args()


# --------------------------------------------------------------------------- #
# qwen_tts module bootstrapping
# --------------------------------------------------------------------------- #


def ensure_qwen_module() -> None:
    """Download the vendored qwen_tts module from the HF Space and expose it.

    Qwen3-TTS does not publish a PyPI package. Its runtime Python code
    lives inside the official Hugging Face Space. We snapshot-download
    the space, put its root on sys.path, and re-try the import.
    """
    try:
        import qwen_tts  # type: ignore  # noqa: F401
        return
    except ImportError:
        pass

    print("Downloading qwen_tts vendored module from HuggingFace Space…")
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is required. Install with: pip install huggingface_hub"
        ) from exc

    space_path = snapshot_download(repo_id=QWEN_TTS_SPACE, repo_type="space")
    if space_path not in sys.path:
        sys.path.insert(0, space_path)

    try:
        import qwen_tts  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            f"Failed to import qwen_tts from downloaded HF Space at {space_path}. "
            f"The Space layout may have changed since this script was written. "
            f"Check the Space manually and update QWEN_TTS_SPACE or the import. "
            f"Original error: {exc}"
        ) from exc


# --------------------------------------------------------------------------- #
# Mode selection (pure, testable)
# --------------------------------------------------------------------------- #


def pick_mode(args: argparse.Namespace) -> tuple[str, str, str, str]:
    """Pick the Qwen3-TTS variant based on which CLI flags are set.

    Returns a tuple of ``(mode, model_id, dtype_name, mode_label)`` where:

    - ``mode`` is one of ``"clone" | "design" | "preset"``
    - ``model_id`` is the HuggingFace repo id for the chosen variant
    - ``dtype_name`` is ``"float16"`` or ``"float32"`` — caller resolves
      the actual ``torch.dtype`` object so this helper stays import-light
      and unit-testable without a torch install
    - ``mode_label`` is a human-readable label for logging

    Precedence is ``--ref-audio`` > ``--voice-description`` > preset.

    **All three modes use float32 on MPS.** float16 causes NaN/inf in
    the sampling step on Apple Silicon — a well-known PyTorch MPS bug
    that surfaces as::

        RuntimeError: probability tensor contains either `inf`, `nan`
        or element < 0

    float32 doubles memory use vs float16 (CustomVoice 0.6B ~2.4 GB,
    VoiceDesign 1.7B ~6.8 GB) but is the only dtype that reliably
    produces audio on MPS.  If you have an NVIDIA GPU and want to use
    float16 for speed, override at the call site.
    """
    if args.ref_audio:
        return (
            "clone",
            MODEL_BASE,
            "float32",
            "voice cloning (Base model)",
        )
    if args.voice_description:
        return (
            "design",
            MODEL_VOICEDESIGN,
            "float32",
            "voice design (VoiceDesign 1.7B)",
        )
    return (
        "preset",
        MODEL_CUSTOM,
        "float32",
        "preset voice (CustomVoice)",
    )


def language_warning(language: str) -> Optional[str]:
    """Return a warning string if the language is unsupported, else None.

    Comparison is case-insensitive because the Qwen3-TTS runtime check
    is lowercase-only.
    """
    if language.lower() in SUPPORTED_LANGUAGES:
        return None
    return (
        f"'{language}' is not in Qwen3-TTS's official supported-language "
        f"list. Expect bad quality or failure. "
        f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
    )


# --------------------------------------------------------------------------- #
# Reference audio loading
# --------------------------------------------------------------------------- #


def load_reference_audio(path: Path):
    """Load a reference WAV/FLAC/OGG as (numpy_array, sample_rate)."""
    try:
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError(
            "soundfile is required for --ref-audio. Install with: pip install soundfile"
        ) from exc

    wav_np, sr = sf.read(str(path))
    return wav_np, int(sr)


# --------------------------------------------------------------------------- #
# Main synthesis pipeline
# --------------------------------------------------------------------------- #


def main() -> None:
    args = parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        sys.exit(f"PDF not found: {pdf_path}")

    if args.ref_audio:
        ref_path = Path(args.ref_audio).expanduser().resolve()
        if not ref_path.exists():
            sys.exit(f"Reference audio not found: {ref_path}")
    else:
        ref_path = None

    # Make the repo root importable so we can reuse the project's PDF parser.
    repo_root = Path(__file__).resolve().parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from src.pdf_parser import parse_pdf
    from src.tts_engine import split_text_into_chunks

    print(f"Parsing {pdf_path.name}…")
    book = parse_pdf(str(pdf_path))
    print(
        f"  {book.metadata.num_pages} pages, "
        f"{book.total_chars} chars, "
        f"{len(book.chapters)} chapters"
    )

    chunks = split_text_into_chunks(book.full_text, max_chars=MAX_CHUNK_CHARS)
    if args.max_chunks > 0:
        chunks = chunks[: args.max_chunks]
    print(f"  split into {len(chunks)} chunks of up to {MAX_CHUNK_CHARS} chars")

    # Lazy-download the vendored qwen_tts module from the HF Space.
    ensure_qwen_module()

    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "torch is required. Install with: pip install torch torchaudio"
        ) from exc

    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("numpy is required. Install with: pip install numpy") from exc

    try:
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError(
            "soundfile is required. Install with: pip install soundfile"
        ) from exc

    from huggingface_hub import snapshot_download
    from qwen_tts import Qwen3TTSModel  # type: ignore

    # Pick model + dtype + mode based on what the user supplied.
    mode, model_id, dtype_name, mode_label = pick_mode(args)
    dtype = torch.float32 if dtype_name == "float32" else torch.float16

    print(f"Mode: {mode_label}")
    print(f"Model: {model_id}")
    print(f"Device: {args.device}   dtype: {dtype}   attn_impl: sdpa")

    # Warn if the user asked for an unsupported language.
    warning = language_warning(args.language)
    if warning:
        print(f"  WARNING: {warning}")

    print("Downloading model weights (cached after first run)…")
    model_path = snapshot_download(repo_id=model_id)

    print(f"Loading model onto {args.device}…")
    try:
        tts = Qwen3TTSModel.from_pretrained(
            model_path,
            device_map=args.device,
            dtype=dtype,
            attn_implementation="sdpa",
        )
    except Exception as exc:  # noqa: BLE001
        sys.exit(
            f"Failed to load {model_id} on {args.device}.\n"
            f"  {type(exc).__name__}: {exc}\n\n"
            "Possible fixes:\n"
            "  - Retry with --device cpu (slow but usually works).\n"
            "  - Qwen3-TTS officially only supports CUDA; neither MPS\n"
            "    nor CPU are covered by their test matrix.\n"
        )

    # Load the reference audio once; voice cloning uses it on every chunk.
    ref_audio_tuple: Optional[tuple] = None
    if ref_path is not None:
        wav_np, sr = load_reference_audio(ref_path)
        ref_audio_tuple = (wav_np, sr)
        duration = len(wav_np) / sr
        print(f"Loaded reference audio: {ref_path.name} ({duration:.1f}s at {sr} Hz)")

    # --- Generation loop -------------------------------------------------- #

    # Qwen3-TTS rejects any language string whose lowercase form is not
    # in its allowlist. Normalise once outside the loop.
    language_param = args.language.lower()

    print(f"Synthesizing {len(chunks)} chunks…")
    all_wavs: list = []
    sample_rate: Optional[int] = None
    start = time.time()

    for i, chunk in enumerate(chunks):
        elapsed = time.time() - start
        preview = chunk[:60].replace("\n", " ")
        print(f"  [{i + 1}/{len(chunks)}] elapsed {elapsed:5.0f}s  — {preview!r}…")

        try:
            if mode == "clone":
                # Base model — voice cloning from a reference audio clip.
                wav, sr = tts.generate_voice_clone(
                    text=chunk,
                    language=language_param,
                    ref_audio=ref_audio_tuple,
                    ref_text="",
                    x_vector_only_mode=False,
                    max_new_tokens=MAX_NEW_TOKENS,
                )
            elif mode == "design":
                # VoiceDesign 1.7B model — natural-language voice description
                # is passed via the `instruct` parameter, not a
                # `voice_description` keyword.  The Space's app.py is the
                # authoritative runtime and uses generate_voice_design().
                wav, sr = tts.generate_voice_design(
                    text=chunk,
                    language=language_param,
                    instruct=args.voice_description,
                    non_streaming_mode=True,
                    max_new_tokens=MAX_NEW_TOKENS,
                )
            else:  # preset
                # CustomVoice model — uses generate_custom_voice() with a
                # preset speaker name.  instruct may also be supplied for
                # mild style steering even in preset mode.
                wav, sr = tts.generate_custom_voice(
                    text=chunk,
                    language=language_param,
                    speaker=args.speaker,
                    instruct=None,
                    non_streaming_mode=True,
                    max_new_tokens=MAX_NEW_TOKENS,
                )
        except Exception as exc:  # noqa: BLE001
            sys.exit(
                f"\nERROR during generation of chunk {i + 1}: "
                f"{type(exc).__name__}: {exc}"
            )

        # Qwen3TTSModel.generate() can return a plain numpy array or a
        # list of waveforms — normalise to a single 1-D numpy array.
        if isinstance(wav, (list, tuple)):
            wav = wav[0]
        arr = np.asarray(wav)
        if arr.ndim > 1:
            arr = arr.squeeze()
        all_wavs.append(arr)
        sample_rate = int(sr)

        # Release MPS memory between chunks.
        if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
            torch.mps.empty_cache()

    if not all_wavs or sample_rate is None:
        sys.exit("No audio generated.")

    combined = np.concatenate(all_wavs)
    out_path = pdf_path.with_suffix(".wav")
    sf.write(str(out_path), combined, sample_rate)

    total = time.time() - start
    duration_s = len(combined) / sample_rate
    print(
        f"\nDone in {total:.0f}s. "
        f"Generated {duration_s:.1f}s of audio at {sample_rate} Hz."
    )
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
