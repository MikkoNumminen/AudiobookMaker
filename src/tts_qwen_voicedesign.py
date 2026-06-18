"""Qwen3-TTS VoiceDesign engine adapter (developer-install-only).

Qwen3-TTS VoiceDesign (https://github.com/QwenLM/Qwen3-TTS) is an open-source
neural TTS that creates a narration voice from a **natural-language
description** — e.g. "a warm male narrator in his mid-30s, calm and measured" —
instead of picking from a fixed voice list. It runs locally on an NVIDIA GPU.

Like the VoxCPM2 adapter, this engine is intentionally **not** added to
requirements.txt and the package is **not** bundled into the Windows installer.
Developers who want it must install it manually inside the source tree:

    pip install qwen-tts

A CUDA-capable NVIDIA GPU is required (~4 GB VRAM for the 1.7B model in
bfloat16). Without one — or without the package installed — the engine reports
itself as unavailable.

Scope decisions baked into this adapter (see docs/qwen_voicedesign_engine.md):

- **VoiceDesign only.** Qwen3-TTS also ships a CustomVoice (preset speakers)
  and a Base/clone (reference-audio) mode. Neither is wired up here — the only
  control is the free-text voice description. ``reference_audio`` is ignored.
- **Finnish is blocked.** The model covers 10 languages and Finnish is not one
  of them. ``supported_languages()`` excludes Finnish and ``synthesize()``
  hard-fails on any unsupported language, so this engine can never be selected
  for a Finnish book.

All heavy imports (`qwen_tts`, `torch`, `soundfile`) live inside the methods
that need them so the main app can start instantly even when none are installed.
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

from src.tts_base import (
    EngineStatus,
    ProgressCallback,
    TTSEngine,
    Voice,
    register_engine,
)
from src.tts_engine import (
    combine_audio_files,
    normalize_text,
    split_text_into_chunks,
)
from src.tts_normalizer import SUPPORTED_LANGS


# ---------------------------------------------------------------------------
# Model + language facts (confirmed from the official repo / HF model card)
# ---------------------------------------------------------------------------

# The natural-language voice-design variant. Confirmed from the Hugging Face
# model card: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign
_HF_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
_INSTALL_HINT = "Install required: pip install qwen-tts  (developer install only)"

# Qwen3-TTS speaks 10 languages. The model's ``generate_voice_design`` wants
# the language NAME (e.g. "English"), so this maps AudiobookMaker's short codes
# to those names. Finnish is deliberately absent — the model cannot speak it,
# and its omission is what keeps this engine from ever being offered for a
# Finnish book.
_QWEN_LANGUAGES: dict[str, str] = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "ru": "Russian",
    "pt": "Portuguese",
    "es": "Spanish",
    "it": "Italian",
}

# Attention + device are configurable via env vars so a developer can opt into
# flash-attention (faster, needs flash-attn built) or a different GPU without a
# code change. Defaults are the safe, no-extra-build path.
_ATTN_ENV = "AUDIOBOOKMAKER_QWEN_ATTN"
_DEVICE_ENV = "AUDIOBOOKMAKER_QWEN_DEVICE"
_DEFAULT_ATTN = "sdpa"
_DEFAULT_DEVICE = "cuda:0"
# Accepted attn_implementation values (the transformers set Qwen3-TTS forwards).
# A typo'd AUDIOBOOKMAKER_QWEN_ATTN otherwise surfaces only as a deep
# from_pretrained traceback after the model-load wait, so validate up front.
_VALID_ATTN = ("sdpa", "flash_attention_2", "eager")


# ---------------------------------------------------------------------------
# Voice presets
# ---------------------------------------------------------------------------

# VoiceDesign has no fixed voices — the "voice" IS the description. To keep the
# engine usable without typing a description every time (and to give the CLI a
# meaningful default voice), a few canned descriptions are exposed as voice ids.
# A free-text ``voice_description`` always overrides the preset's text.
#
# voice_id -> (display_name, instruct)
_VOICE_PRESETS: dict[str, tuple[str, str]] = {
    "qwen-neutral-narrator": (
        "Qwen neutral narrator",
        "A clear, neutral narrator with even pacing and natural intonation, "
        "well suited to reading an audiobook.",
    ),
    "qwen-warm-male": (
        "Qwen warm male narrator",
        "A warm male narrator in his mid-30s, calm and measured, with a "
        "friendly, reassuring tone.",
    ),
    "qwen-bright-female": (
        "Qwen bright female narrator",
        "A bright, expressive young woman with a light, energetic voice and "
        "clear diction.",
    ),
}
_DEFAULT_VOICE_ID = "qwen-neutral-narrator"


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@register_engine
class QwenVoiceDesignEngine(TTSEngine):
    """Qwen3-TTS VoiceDesign — describe a voice in words, requires GPU.

    Developer-only engine: the Windows installer does not bundle it because
    torch + the model weights are several gigabytes. Run the project from
    source and ``pip install qwen-tts`` to enable it.
    """

    id = "qwen_voicedesign"
    display_name = "Qwen3-TTS VoiceDesign (describe-a-voice, requires GPU)"
    description = (
        "Local neural TTS that builds a voice from a natural-language "
        "description. 10 languages (no Finnish). Requires an NVIDIA GPU and a "
        "manual `pip install qwen-tts`."
    )
    requires_gpu = True
    # First run downloads the weights from Hugging Face; after they are cached
    # the engine runs fully offline, so this mirrors VoxCPM2's reasoning.
    requires_internet = False
    # VoiceDesign deliberately exposes neither reference-audio cloning nor
    # preset-speaker selection — only the free-text description.
    supports_voice_cloning = False
    supports_voice_description = True

    def __init__(self) -> None:
        super().__init__()
        self._model = None  # lazy: loaded on first synthesize()

    # --------------------------------------------------------------------- #
    # Status
    # --------------------------------------------------------------------- #

    def check_status(self) -> EngineStatus:
        # All checks use lazy imports so this stays cheap to call repeatedly
        # from the GUI/CLI without dragging torch into the process.
        try:
            import qwen_tts  # noqa: F401
        except ImportError:
            return EngineStatus(available=False, reason=_INSTALL_HINT)

        try:
            import torch
        except ImportError:
            return EngineStatus(
                available=False,
                reason="Install required: pip install torch  (and a CUDA build)",
            )

        try:
            cuda_ok = bool(torch.cuda.is_available())
        except Exception:
            cuda_ok = False

        if not cuda_ok:
            return EngineStatus(
                available=False,
                reason="Requires NVIDIA GPU with CUDA (~4 GB VRAM).",
            )

        return EngineStatus(available=True)

    # --------------------------------------------------------------------- #
    # Voices / languages
    # --------------------------------------------------------------------- #

    def supported_languages(self) -> set[str]:
        # The 10 languages Qwen3-TTS covers. Finnish is intentionally absent.
        return set(_QWEN_LANGUAGES)

    def list_voices(self, language: str) -> list[Voice]:
        if language not in _QWEN_LANGUAGES:
            return []
        return [
            Voice(
                id=voice_id,
                display_name=display_name,
                language=language,
                gender="",
            )
            for voice_id, (display_name, _instruct) in _VOICE_PRESETS.items()
        ]

    def default_voice(self, language: str) -> Optional[str]:
        if language not in _QWEN_LANGUAGES:
            return None
        return _DEFAULT_VOICE_ID

    # --------------------------------------------------------------------- #
    # Synthesis
    # --------------------------------------------------------------------- #

    def _load_model(self):
        """Load the VoiceDesign model on first use and cache on the instance."""
        if self._model is not None:
            return self._model

        # Lazy heavy imports — torch + the model weights are several GB.
        import torch
        from qwen_tts import Qwen3TTSModel  # type: ignore[import-not-found]

        device = os.environ.get(_DEVICE_ENV, _DEFAULT_DEVICE)
        attn = os.environ.get(_ATTN_ENV, _DEFAULT_ATTN)
        if attn not in _VALID_ATTN:
            raise ValueError(
                f"{_ATTN_ENV}={attn!r} is not a valid attention implementation; "
                f"choose one of {', '.join(_VALID_ATTN)}."
            )
        self._model = Qwen3TTSModel.from_pretrained(
            _HF_MODEL_ID,
            device_map=device,
            dtype=torch.bfloat16,
            attn_implementation=attn,
        )
        return self._model

    def synthesize(
        self,
        text: str,
        output_path: str,
        voice_id: str,
        language: str,
        progress_cb: Optional[ProgressCallback] = None,
        reference_audio: Optional[str] = None,
        voice_description: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> None:
        # rate is ignored: VoiceDesign has no speed-control parameter.
        # reference_audio is ignored on purpose: cloning is out of scope for
        # this engine (VoiceDesign only — see the module docstring).
        if not text or not text.strip():
            raise ValueError("Cannot synthesize empty text.")

        # Language guard. The model speaks 10 languages; Finnish (and anything
        # else not in the map) is hard-blocked so this engine can never be used
        # for a Finnish book.
        qwen_language = _QWEN_LANGUAGES.get(language)
        if qwen_language is None:
            supported = ", ".join(sorted(_QWEN_LANGUAGES))
            raise ValueError(
                f"Qwen3-TTS VoiceDesign does not support language '{language}'. "
                f"Supported languages: {supported}. Finnish is not available "
                "on this engine."
            )

        if not voice_id:
            # default_voice is guaranteed non-None here: the language guard
            # above already proved `language` is one of _QWEN_LANGUAGES.
            voice_id = self.default_voice(language)
        if voice_id not in _VOICE_PRESETS:
            raise ValueError(f"Unknown Qwen VoiceDesign voice id: {voice_id}")

        # An explicit free-text description wins; otherwise fall back to the
        # selected preset's canned description.
        instruct = _resolve_instruct(voice_description, voice_id)

        # Re-check availability so the user gets a clear error instead of an
        # opaque ImportError when they skipped the status line.
        status = self.check_status()
        if not status.available:
            raise RuntimeError(
                f"Qwen3-TTS VoiceDesign unavailable: {status.reason}"
            )

        # soundfile is pulled in transitively by qwen-tts.
        import soundfile as sf  # type: ignore[import-not-found]

        if progress_cb:
            progress_cb(0, 0, "Loading Qwen3-TTS VoiceDesign model (~4 GB VRAM)…")
        model = self._load_model()

        # Normalize before chunking only for languages the normalizer actually
        # handles (Finnish/English). The other Qwen languages (zh, ja, ko, …)
        # have no normalizer and ``normalize_text`` would raise on them, so
        # they pass through unmodified — an unnormalized read is correct, a
        # mis-normalized one is not.
        if language in SUPPORTED_LANGS:
            text = normalize_text(text, language)
        chunks = split_text_into_chunks(text)
        if not chunks:
            raise ValueError("Text produced no chunks after splitting.")

        with tempfile.TemporaryDirectory(prefix="qwen_vd_") as tmp_dir:
            chunk_paths: list[str] = []
            total = len(chunks)

            for i, chunk in enumerate(chunks):
                if progress_cb:
                    progress_cb(i, total, f"Synthesizing chunk {i + 1}/{total}…")

                # generate_voice_design returns (list_of_waveforms, sample_rate).
                wavs, sr = model.generate_voice_design(
                    text=chunk,
                    language=qwen_language,
                    instruct=instruct,
                )
                if not wavs:
                    raise RuntimeError(
                        f"Qwen3-TTS returned no audio for chunk {i + 1}/{total}."
                    )
                # The return dtype/device is not a documented contract, so coerce
                # to a flat 1-D CPU float32 array: soundfile cannot write a CUDA
                # or bfloat16 buffer, and a [1, N] shape would be mis-read. Trust
                # the model-reported rate, cast to int for libsndfile.
                wav = _to_cpu_float32_mono(wavs[0])

                chunk_path = os.path.join(tmp_dir, f"chunk_{i:04d}.wav")
                sf.write(chunk_path, wav, int(sr))
                chunk_paths.append(chunk_path)

            if progress_cb:
                progress_cb(total, total, "Combining audio files…")
            combine_audio_files(chunk_paths, output_path)

        if progress_cb:
            progress_cb(total, total, "Done!")


def _to_cpu_float32_mono(wav):
    """Coerce a model waveform to a flat 1-D CPU float32 numpy array.

    The exact return type of ``generate_voice_design`` is not a documented
    contract — it may be a CUDA/bfloat16 torch tensor or a numpy array, shaped
    ``[N]`` or ``[1, N]``. soundfile can only serialize a CPU float array, so we
    normalize here rather than assume the happy shape.
    """
    import numpy as np

    if hasattr(wav, "detach"):  # a torch tensor — move to CPU, cast bf16 -> f32
        wav = wav.detach().to("cpu").float().numpy()
    return np.asarray(wav, dtype=np.float32).reshape(-1)


def _resolve_instruct(voice_description: Optional[str], voice_id: str) -> str:
    """Pick the natural-language instruction passed to the model.

    A non-empty ``voice_description`` (free-text, from the user) always wins.
    Otherwise the selected preset's canned description is used. ``voice_id`` is
    guaranteed to be a known preset by the caller.
    """
    if voice_description and voice_description.strip():
        return voice_description.strip()
    return _VOICE_PRESETS[voice_id][1]
