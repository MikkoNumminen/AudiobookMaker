"""VoxCPM2 TTS engine adapter (developer-install-only).

VoxCPM2 (https://github.com/OpenBMB/VoxCPM) is an open-source neural TTS
that supports 30 languages including Finnish, runs locally, and supports
zero-shot voice cloning from a short reference audio sample.

This adapter is intentionally **not** added to requirements.txt and the
package is **not** bundled into the Windows installer. Users who want to
try it must install it manually inside the source tree:

    pip install voxcpm

A CUDA-capable NVIDIA GPU with ~8 GB VRAM is required. Without one (or
without the package installed) the engine reports itself as unavailable
and the GUI shows the install instructions.

All heavy imports (`voxcpm`, `torch`, `soundfile`) live inside the
methods that need them so the main app can start instantly even when
none of the above are installed.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from src.tts_base import (
    EngineStatus,
    ProgressCallback,
    TTSEngine,
    Voice,
    register_engine,
)
from src.tts_engine import combine_audio_files, split_text_into_chunks


# ---------------------------------------------------------------------------
# Voice catalogue
# ---------------------------------------------------------------------------

# VoxCPM2 ships one built-in voice that works across all 30 languages.
# We expose one entry per supported language so the GUI dropdown stays
# meaningful, but they all map to the same underlying model. Voice cloning
# uses a separate reference_audio file picker (handled by the GUI), not a
# different voice id.
#
# language_code -> (voice_id, display_name)
_DEFAULT_VOICES: dict[str, tuple[str, str]] = {
    "fi": ("voxcpm2-default-fi", "VoxCPM2 oletusääni (suomi)"),
    "en": ("voxcpm2-default-en", "VoxCPM2 default voice (English)"),
}

_HUGGINGFACE_MODEL_ID = "openbmb/VoxCPM2"
_INSTALL_HINT = "Install required: pip install voxcpm  (developer install only)"


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@register_engine
class VoxCPM2Engine(TTSEngine):
    """OpenBMB VoxCPM2 — best quality, supports voice cloning, requires GPU.

    This is a developer-only engine. The Windows installer does not bundle
    it because torch + the model weights are several gigabytes. Run the
    project from source and `pip install voxcpm` to enable it.
    """

    id = "voxcpm2"
    display_name = "VoxCPM2 (best quality + voice cloning, requires GPU)"
    description = (
        "Local neural TTS with zero-shot voice cloning. 30 languages "
        "including Finnish. Requires an NVIDIA GPU and a manual `pip "
        "install voxcpm`."
    )
    requires_gpu = True
    requires_internet = False
    supports_voice_cloning = True
    supports_voice_description = True

    # The loaded VoxCPM2 model is cached on the instance after the first
    # synthesize() call. The class is instantiated fresh by list_engines()
    # so this cache only matters within a single conversion job.
    def __init__(self) -> None:
        super().__init__()
        self._model = None  # lazy: loaded on first synthesize()
        self._sample_rate: Optional[int] = None

    # --------------------------------------------------------------------- #
    # Status
    # --------------------------------------------------------------------- #

    def check_status(self) -> EngineStatus:
        # All three checks use lazy imports so this method is cheap to call
        # repeatedly from the GUI without dragging torch into the process.
        try:
            import voxcpm  # noqa: F401
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
                reason="Requires NVIDIA GPU with CUDA (~8 GB VRAM).",
            )

        return EngineStatus(available=True)

    # --------------------------------------------------------------------- #
    # Voices
    # --------------------------------------------------------------------- #

    def list_voices(self, language: str) -> list[Voice]:
        spec = _DEFAULT_VOICES.get(language)
        if spec is None:
            return []
        voice_id, display_name = spec
        return [
            Voice(
                id=voice_id,
                display_name=display_name,
                language=language,
                gender="",
            )
        ]

    def default_voice(self, language: str) -> Optional[str]:
        spec = _DEFAULT_VOICES.get(language)
        return spec[0] if spec else None

    # --------------------------------------------------------------------- #
    # Synthesis
    # --------------------------------------------------------------------- #

    def _load_model(self):
        """Load the VoxCPM2 model on first use and cache on the instance."""
        if self._model is not None:
            return self._model

        # Lazy heavy imports — both ~2 GB on disk together with torch.
        from voxcpm import VoxCPM  # type: ignore[import-not-found]

        self._model = VoxCPM.from_pretrained(_HUGGINGFACE_MODEL_ID, load_denoiser=False)
        # The model exposes its sample rate via tts_model.sample_rate.
        self._sample_rate = int(self._model.tts_model.sample_rate)
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
    ) -> None:
        if not text or not text.strip():
            raise ValueError("Cannot synthesize empty text.")

        if not voice_id:
            voice_id = self.default_voice(language) or ""
        if voice_id and voice_id not in {v[0] for v in _DEFAULT_VOICES.values()}:
            raise ValueError(f"Unknown VoxCPM2 voice id: {voice_id}")

        # Optional reference audio for cloning. If provided, sanity-check it.
        if reference_audio and not os.path.exists(reference_audio):
            raise ValueError(f"Reference audio not found: {reference_audio}")

        # Re-check availability so the user gets a clear error instead of
        # an opaque ImportError when they didn't read the GUI status line.
        status = self.check_status()
        if not status.available:
            raise RuntimeError(f"VoxCPM2 unavailable: {status.reason}")

        # soundfile is pulled in transitively by voxcpm.
        import soundfile as sf  # type: ignore[import-not-found]

        if progress_cb:
            progress_cb(0, 0, "Ladataan VoxCPM2-mallia (~8 GB VRAM)…")
        model = self._load_model()
        sample_rate = self._sample_rate or 24000

        # VoxCPM2's voice-description feature is implemented by prepending
        # the description in parentheses to the text itself, e.g.:
        #   text = "(A warm baritone elderly male)Hello there."
        # We normalise whatever the user typed into that form and apply it
        # to every chunk so the whole audiobook stays consistent.
        description_prefix = _build_description_prefix(voice_description)

        chunks = split_text_into_chunks(text)
        if not chunks:
            raise ValueError("Text produced no chunks after splitting.")

        with tempfile.TemporaryDirectory(prefix="voxcpm_") as tmp_dir:
            chunk_paths: list[str] = []
            total = len(chunks)

            for i, chunk in enumerate(chunks):
                if progress_cb:
                    progress_cb(i, total, f"Syntetisoidaan pala {i + 1}/{total}…")

                prompt_text = f"{description_prefix}{chunk}" if description_prefix else chunk

                # VoxCPM.generate() returns a numpy float32 waveform.
                if reference_audio:
                    wav = model.generate(
                        text=prompt_text,
                        reference_wav_path=reference_audio,
                    )
                else:
                    wav = model.generate(text=prompt_text)

                chunk_path = os.path.join(tmp_dir, f"chunk_{i:04d}.wav")
                sf.write(chunk_path, wav, sample_rate)
                chunk_paths.append(chunk_path)

            if progress_cb:
                progress_cb(total, total, "Yhdistetään äänitiedostot…")
            combine_audio_files(chunk_paths, output_path)

        if progress_cb:
            progress_cb(total, total, "Valmis!")


def _build_description_prefix(voice_description: Optional[str]) -> str:
    """Format a user-supplied voice description into VoxCPM2's prefix form.

    Accepts:
      - None / empty / whitespace-only  -> returns "" (no-op)
      - "warm baritone"                 -> "(warm baritone)"
      - "(warm baritone)"               -> "(warm baritone)"
      - "  (  warm baritone  )  "       -> "(warm baritone)"

    The returned string is ready to be concatenated directly in front of
    a text chunk so that `prompt = prefix + chunk`.
    """
    if not voice_description:
        return ""
    stripped = voice_description.strip()
    if not stripped:
        return ""
    # Tolerate users who already wrote the parentheses themselves.
    if stripped.startswith("(") and stripped.endswith(")"):
        stripped = stripped[1:-1].strip()
        if not stripped:
            return ""
    return f"({stripped})"
