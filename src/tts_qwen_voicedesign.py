"""Qwen3-TTS VoiceDesign engine adapter (developer-install-only).

Qwen3-TTS VoiceDesign (https://github.com/QwenLM/Qwen3-TTS) is an open-source
neural TTS that creates a narration voice from a **natural-language
description** — e.g. "a warm male narrator in his mid-30s, calm and measured" —
instead of picking from a fixed voice list. It runs locally on an NVIDIA GPU.

Like the VoxCPM2 adapter, this engine is intentionally **not** added to
requirements.txt and the package is **not** bundled into the Windows installer.
Developers who want it must install it manually inside the source tree:

    pip install qwen-tts

The shared plumbing (model load, language guard, chunk/coerce/combine pipeline)
lives in ``src/tts_qwen_common.py``; this module only adds the VoiceDesign
specifics: the preset described voices and the ``generate_voice_design`` call.

Scope decisions baked into this adapter (see docs/qwen_voicedesign_engine.md):

- **VoiceDesign only.** The reference-audio cloning path is the separate
  CustomVoice/Base flow, not wired here — the only control is the free-text
  description, so ``reference_audio`` is ignored.
- **Finnish is blocked** (enforced by the shared base): Finnish is not one of
  the model's 10 languages, so it can never be selected for a Finnish book.
"""

from __future__ import annotations

from typing import Optional

from src.tts_base import Voice, register_engine
from src.tts_qwen_common import QWEN_LANGUAGES as _QWEN_LANGUAGES, QwenEngineBase


# The natural-language voice-design variant. Confirmed from the Hugging Face
# model card: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign
_HF_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"


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
class QwenVoiceDesignEngine(QwenEngineBase):
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
    supports_voice_description = True

    MODEL_ID = _HF_MODEL_ID
    _LABEL = "Qwen3-TTS VoiceDesign"
    _TMP_PREFIX = "qwen_vd_"

    # --- voices --------------------------------------------------------- #

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

    # --- per-mode hooks ------------------------------------------------- #

    def _resolve_voice(self, voice_id: str, language: str) -> str:
        if not voice_id:
            # default_voice is non-None here: the base's language guard already
            # proved `language` is one of _QWEN_LANGUAGES.
            voice_id = self.default_voice(language)
        if voice_id not in _VOICE_PRESETS:
            raise ValueError(f"Unknown Qwen VoiceDesign voice id: {voice_id}")
        return voice_id

    def _prepare_generation(
        self,
        voice_id: str,
        voice_description: Optional[str],
        reference_audio: Optional[str] = None,  # ignored: VoiceDesign has no cloning
    ) -> dict:
        # An explicit free-text description wins; otherwise fall back to the
        # selected preset's canned description.
        return {"instruct": _resolve_instruct(voice_description, voice_id)}

    def _generate(self, model, chunk: str, qwen_language: str, prepared: dict):
        return model.generate_voice_design(
            text=chunk,
            language=qwen_language,
            instruct=prepared["instruct"],
        )


def _resolve_instruct(voice_description: Optional[str], voice_id: str) -> str:
    """Pick the natural-language instruction passed to the model.

    A non-empty ``voice_description`` (free-text, from the user) always wins.
    Otherwise the selected preset's canned description is used. ``voice_id`` is
    guaranteed to be a known preset by the caller.
    """
    if voice_description and voice_description.strip():
        return voice_description.strip()
    return _VOICE_PRESETS[voice_id][1]
