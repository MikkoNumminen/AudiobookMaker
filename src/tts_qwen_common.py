"""Shared plumbing for the developer-only Qwen3-TTS engines.

Qwen3-TTS ships several checkpoints that all load through the same
``qwen_tts.Qwen3TTSModel`` class, speak the same 10 languages (no Finnish), and
share the chunk → generate → coerce → combine pipeline. Only the per-chunk
generate call and the voice catalogue differ between modes:

- VoiceDesign  (``src/tts_qwen_voicedesign.py``) — describe a voice in words.
- CustomVoice  (``src/tts_qwen_customvoice.py``) — pick a preset speaker.
- (Clone/Base — planned) — read text in a voice copied from a reference clip.

This module holds the parts they all share so each engine is a thin subclass of
:class:`QwenEngineBase` rather than a copy-paste. Like VoxCPM2, these engines are
**not** in requirements.txt and **not** bundled in the installer; they need an
NVIDIA GPU and a manual ``pip install qwen-tts``.

All heavy imports (``qwen_tts``, ``torch``, ``soundfile``, ``numpy``) live inside
the methods that need them so the main app starts instantly when none are present.
"""

from __future__ import annotations

import os
import tempfile
from abc import abstractmethod
from typing import ClassVar, Optional

from src.tts_base import (
    EngineStatus,
    ProgressCallback,
    TTSEngine,
    Voice,
)
from src.tts_engine import (
    combine_audio_files,
    normalize_text,
    split_text_into_chunks,
)
from src.tts_normalizer import SUPPORTED_LANGS


# ---------------------------------------------------------------------------
# Model + language facts (confirmed from the official repo / HF model cards)
# ---------------------------------------------------------------------------

INSTALL_HINT = "Install required: pip install qwen-tts  (developer install only)"

# Qwen3-TTS speaks 10 languages. The generate_* methods want the language NAME
# (e.g. "English"), so this maps AudiobookMaker's short codes to those names.
# Finnish is deliberately absent — the model cannot speak it, and its omission is
# what keeps these engines from ever being offered for a Finnish book.
QWEN_LANGUAGES: dict[str, str] = {
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
# Shared helpers
# ---------------------------------------------------------------------------


def qwen_check_status(install_hint: str = INSTALL_HINT) -> EngineStatus:
    """Report whether qwen-tts + torch + CUDA are usable. Cheap (lazy imports)."""
    try:
        import qwen_tts  # noqa: F401
    except ImportError:
        return EngineStatus(available=False, reason=install_hint)

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


def qwen_device() -> str:
    """The device string for Qwen loads (``AUDIOBOOKMAKER_QWEN_DEVICE``).

    Single source of the device setting so every Qwen engine — including the
    clone engine's Whisper transcription — targets the same GPU.
    """
    return os.environ.get(_DEVICE_ENV, _DEFAULT_DEVICE)


def load_qwen_model(model_id: str):
    """Load a Qwen3TTSModel checkpoint with the env-configured device/attn.

    ``device`` is intentionally not validated: device_map accepts many forms
    (cuda, cuda:N, cpu, auto) and a clean torch.device() parse still can't
    confirm the GPU index exists, so an up-front check would be leaky. ``attn``
    is a closed three-value set, so it is worth validating before the (slow)
    load.
    """
    import torch
    from qwen_tts import Qwen3TTSModel  # type: ignore[import-not-found]

    device = qwen_device()
    attn = os.environ.get(_ATTN_ENV, _DEFAULT_ATTN)
    if attn not in _VALID_ATTN:
        raise ValueError(
            f"{_ATTN_ENV}={attn!r} is not a valid attention implementation; "
            f"choose one of {', '.join(_VALID_ATTN)}."
        )
    return Qwen3TTSModel.from_pretrained(
        model_id,
        device_map=device,
        dtype=torch.bfloat16,
        attn_implementation=attn,
    )


def to_cpu_float32_mono(wav):
    """Coerce a model waveform to a flat 1-D CPU float32 numpy array.

    The exact return type of the generate_* methods is not a documented
    contract — it may be a CUDA/bfloat16 torch tensor or a numpy array, shaped
    ``[N]`` or ``[1, N]``. soundfile can only serialize a CPU float array, so we
    normalize here rather than assume the happy shape.
    """
    import numpy as np

    if hasattr(wav, "detach"):  # a torch tensor — move to CPU, cast bf16 -> f32
        wav = wav.detach().to("cpu").float().numpy()
    return np.asarray(wav, dtype=np.float32).reshape(-1)


# ---------------------------------------------------------------------------
# Base engine
# ---------------------------------------------------------------------------


class QwenEngineBase(TTSEngine):
    """Shared skeleton for the Qwen3-TTS engines.

    Subclasses set ``MODEL_ID`` / ``id`` / ``display_name`` / ``description``,
    provide their voice catalogue (``list_voices`` / ``default_voice``), and
    implement three small hooks: :meth:`_resolve_voice` (default-fill + validate
    the voice id), :meth:`_prepare_generation` (turn the voice id + description
    into the per-call kwargs), and :meth:`_generate` (call the right generate_*
    method). Everything else — the language guard, normalization, chunking,
    waveform coercion, and stitching — is shared here.
    """

    MODEL_ID: ClassVar[str]
    requires_gpu = True
    # First run downloads the weights from Hugging Face; after they are cached
    # the engine runs fully offline, mirroring VoxCPM2's reasoning.
    requires_internet = False
    supports_voice_cloning = False

    # Subclasses override these for clearer messages / temp dirs.
    _LABEL: ClassVar[str] = "Qwen3-TTS"
    _TMP_PREFIX: ClassVar[str] = "qwen_"

    def __init__(self) -> None:
        super().__init__()
        self._model = None  # lazy: loaded on first synthesize()

    # --- shared implementations ---------------------------------------- #

    def check_status(self) -> EngineStatus:
        return qwen_check_status(INSTALL_HINT)

    def supported_languages(self) -> set[str]:
        # The 10 languages Qwen3-TTS covers. Finnish is intentionally absent.
        return set(QWEN_LANGUAGES)

    def _load_model(self):
        if self._model is None:
            self._model = load_qwen_model(self.MODEL_ID)
        return self._model

    # --- per-mode hooks ------------------------------------------------- #

    @abstractmethod
    def _resolve_voice(self, voice_id: str, language: str) -> str:
        """Fill the default voice when empty and validate it; raise on unknown.

        Called after the language guard, so ``language in QWEN_LANGUAGES`` holds.
        """

    @abstractmethod
    def _prepare_generation(
        self,
        voice_id: str,
        voice_description: Optional[str],
        reference_audio: Optional[str],
    ) -> dict:
        """Return an opaque dict of per-call state (resolved once, reused per
        chunk) — e.g. the instruct text, speaker id, or cloning ref/transcript.

        Runs before model load, so this is also where a mode validates its own
        inputs (raising ValueError) — e.g. the Clone engine requires
        ``reference_audio`` here. Engines that don't clone ignore it.
        """

    @abstractmethod
    def _generate(self, model, chunk: str, qwen_language: str, prepared: dict):
        """Call the mode's generate_* method; return ``(wavs, sr)``."""

    # --- shared synthesis driver --------------------------------------- #

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
        # rate is ignored: Qwen3-TTS has no speed-control parameter. Engines
        # that don't clone ignore reference_audio (see each subclass).
        if not text or not text.strip():
            raise ValueError("Cannot synthesize empty text.")

        # Language guard. The model speaks 10 languages; Finnish (and anything
        # else not in the map) is hard-blocked so these engines can never be
        # used for a Finnish book.
        qwen_language = QWEN_LANGUAGES.get(language)
        if qwen_language is None:
            supported = ", ".join(sorted(QWEN_LANGUAGES))
            raise ValueError(
                f"{self._LABEL} does not support language '{language}'. "
                f"Supported languages: {supported}. Finnish is not available "
                "on this engine."
            )

        voice_id = self._resolve_voice(voice_id, language)
        prepared = self._prepare_generation(
            voice_id, voice_description, reference_audio
        )

        # Re-check availability so the user gets a clear error instead of an
        # opaque ImportError when they skipped the status line.
        status = self.check_status()
        if not status.available:
            raise RuntimeError(f"{self._LABEL} unavailable: {status.reason}")

        # soundfile is pulled in transitively by qwen-tts.
        import soundfile as sf  # type: ignore[import-not-found]

        if progress_cb:
            progress_cb(0, 0, f"Loading {self._LABEL} model (~4 GB VRAM)…")
        model = self._load_model()

        # Normalize before chunking only for languages the normalizer actually
        # handles (Finnish/English). The other Qwen languages (zh, ja, ko, …)
        # have no normalizer and ``normalize_text`` would raise on them, so they
        # pass through unmodified — an unnormalized read is correct, a
        # mis-normalized one is not.
        if language in SUPPORTED_LANGS:
            text = normalize_text(text, language)
        chunks = split_text_into_chunks(text)
        if not chunks:
            raise ValueError("Text produced no chunks after splitting.")

        with tempfile.TemporaryDirectory(prefix=self._TMP_PREFIX) as tmp_dir:
            chunk_paths: list[str] = []
            total = len(chunks)

            for i, chunk in enumerate(chunks):
                if progress_cb:
                    progress_cb(i, total, f"Synthesizing chunk {i + 1}/{total}…")

                wavs, sr = self._generate(model, chunk, qwen_language, prepared)
                if not wavs:
                    raise RuntimeError(
                        f"{self._LABEL} returned no audio for chunk "
                        f"{i + 1}/{total}."
                    )
                # The return dtype/device is not a documented contract, so coerce
                # to a flat 1-D CPU float32 array: soundfile cannot write a CUDA
                # or bfloat16 buffer, and a [1, N] shape would be mis-read. Trust
                # the model-reported rate, cast to int for libsndfile.
                wav = to_cpu_float32_mono(wavs[0])

                chunk_path = os.path.join(tmp_dir, f"chunk_{i:04d}.wav")
                sf.write(chunk_path, wav, int(sr))
                chunk_paths.append(chunk_path)

            if progress_cb:
                progress_cb(total, total, "Combining audio files…")
            combine_audio_files(chunk_paths, output_path)

        if progress_cb:
            progress_cb(total, total, "Done!")
