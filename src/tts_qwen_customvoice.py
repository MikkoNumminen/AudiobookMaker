"""Qwen3-TTS CustomVoice engine adapter (developer-install-only).

CustomVoice (https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) is the
preset-speaker mode of Qwen3-TTS: instead of describing a voice, you pick one of
9 built-in premium speakers and optionally steer the delivery with a free-text
style instruction (e.g. "read this in an excited tone"). It is a **separate
checkpoint** from VoiceDesign (its own ~4 GB download) but loads through the same
``Qwen3TTSModel`` class.

Like VoiceDesign, this is a developer-only GPU engine — not in requirements.txt,
not bundled in the installer, never the default, and blocked for Finnish. Enable
it with ``pip install qwen-tts``. Shared plumbing lives in
``src/tts_qwen_common.py``.
"""

from __future__ import annotations

from typing import Optional

from src.tts_base import Voice, register_engine
from src.tts_qwen_common import QWEN_LANGUAGES, QwenEngineBase


# The preset-speaker variant. Confirmed from the Hugging Face model card:
# https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice
_HF_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"

# The 9 built-in speakers, confirmed from the model card. Each speaker can speak
# any of the model's 10 languages (best quality in its native language). Gender
# is inferred from the speaker name for the GUI picker; it is cosmetic only.
#   speaker id -> gender
_SPEAKERS: dict[str, str] = {
    "Vivian": "female",
    "Serena": "female",
    "Uncle_Fu": "male",
    "Dylan": "male",
    "Eric": "male",
    "Ryan": "male",
    "Aiden": "male",
    "Ono_Anna": "female",
    "Sohee": "female",
}
_DEFAULT_SPEAKER = "Vivian"


@register_engine
class QwenCustomVoiceEngine(QwenEngineBase):
    """Qwen3-TTS CustomVoice — 9 preset speakers + optional style, requires GPU.

    Developer-only engine (separate ~4 GB checkpoint from VoiceDesign). Run the
    project from source and ``pip install qwen-tts`` to enable it.
    """

    id = "qwen_customvoice"
    display_name = "Qwen3-TTS CustomVoice (preset speakers, requires GPU)"
    description = (
        "Local neural TTS with 9 built-in preset speakers and optional style "
        "steering. 10 languages (no Finnish). Requires an NVIDIA GPU and a "
        "manual `pip install qwen-tts`."
    )
    # voice_description steers the delivery STYLE/emotion here (it does not
    # change which speaker is used — the speaker is the voice_id). Optional.
    supports_voice_description = True

    MODEL_ID = _HF_MODEL_ID
    _LABEL = "Qwen3-TTS CustomVoice"
    _TMP_PREFIX = "qwen_cv_"

    # --- voices --------------------------------------------------------- #

    def list_voices(self, language: str) -> list[Voice]:
        if language not in QWEN_LANGUAGES:
            return []
        return [
            Voice(
                id=speaker,
                display_name=f"{speaker.replace('_', ' ')} ({gender})",
                language=language,
                gender=gender,
            )
            for speaker, gender in _SPEAKERS.items()
        ]

    def default_voice(self, language: str) -> Optional[str]:
        if language not in QWEN_LANGUAGES:
            return None
        return _DEFAULT_SPEAKER

    # --- per-mode hooks ------------------------------------------------- #

    def _resolve_voice(self, voice_id: str, language: str) -> str:
        if not voice_id:
            # Non-None here: the base's language guard already proved `language`
            # is one of QWEN_LANGUAGES.
            voice_id = self.default_voice(language)
        if voice_id not in _SPEAKERS:
            raise ValueError(f"Unknown Qwen CustomVoice speaker: {voice_id}")
        return voice_id

    def _prepare_generation(
        self,
        voice_id: str,
        voice_description: Optional[str],
        reference_audio: Optional[str] = None,  # ignored: CustomVoice has no cloning
    ) -> dict:
        # The speaker is the voice; an optional free-text description becomes the
        # style ``instruct`` (omitted entirely when not supplied).
        instruct = (
            voice_description.strip()
            if voice_description and voice_description.strip()
            else None
        )
        return {"speaker": voice_id, "instruct": instruct}

    def _generate(self, model, chunk: str, qwen_language: str, prepared: dict):
        kwargs = {
            "text": chunk,
            "language": qwen_language,
            "speaker": prepared["speaker"],
        }
        # instruct is optional for CustomVoice — only pass it when given.
        if prepared["instruct"]:
            kwargs["instruct"] = prepared["instruct"]
        return model.generate_custom_voice(**kwargs)
