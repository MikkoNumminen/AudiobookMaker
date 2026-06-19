"""Qwen3-TTS voice-clone engine adapter (developer-install-only).

The Base checkpoint (https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base) clones
a voice from a short **reference clip**: give it a few seconds of someone's
speech and it reads your text in that voice. It is a **separate ~4 GB checkpoint**
from VoiceDesign/CustomVoice but loads through the same ``Qwen3TTSModel`` class.

Qwen's ``generate_voice_clone`` wants the reference *transcript* (``ref_text``)
for best quality. AudiobookMaker only hands an engine the reference audio, so
this adapter fills ``ref_text`` itself, in priority order:

1. an explicit transcript from ``AUDIOBOOKMAKER_QWEN_REF_TEXT`` (skip Whisper);
2. otherwise auto-transcribe the clip with faster-whisper (best quality);
3. if faster-whisper is absent or fails, fall back to ``x_vector_only_mode``
   (no transcript needed, slightly lower quality) so the engine still works.

Security / ethics: this is a **developer-only, local** engine — same dev-only,
``sys.frozen``-gated, installer-excluded, never-default, Finnish-blocked
treatment as the other Qwen engines. Cloned voices are for local use; do not
redistribute clones of real people. The reference transcript is treated as
potentially personal and is **never logged**.

Enable with ``pip install qwen-tts`` (and ``pip install faster-whisper`` for the
auto-transcribe path). Shared plumbing lives in ``src/tts_qwen_common.py``.
"""

from __future__ import annotations

import os
from typing import Optional

from src.tts_base import Voice, register_engine
from src.tts_qwen_common import QwenEngineBase


# The voice-cloning (Base) variant. Confirmed from the Hugging Face model card:
# https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base
_HF_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"

# Explicit reference transcript (skips Whisper) and the Whisper model size used
# when auto-transcribing. Both optional.
_REF_TEXT_ENV = "AUDIOBOOKMAKER_QWEN_REF_TEXT"
_WHISPER_MODEL_ENV = "AUDIOBOOKMAKER_QWEN_WHISPER_MODEL"
_DEFAULT_WHISPER_MODEL = "small"


def _transcribe_reference(ref_path: str) -> Optional[str]:
    """Transcribe the reference clip with faster-whisper to get ``ref_text``.

    Returns ``None`` when faster-whisper is not installed or transcription
    fails, so the caller can fall back to ``x_vector_only_mode``. The transcript
    is returned to the caller but never logged here — it can contain personal
    speech.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError:
        return None

    model_size = os.environ.get(_WHISPER_MODEL_ENV, _DEFAULT_WHISPER_MODEL)
    try:
        whisper = WhisperModel(model_size, device="cuda", compute_type="float16")
        segments, _info = whisper.transcribe(ref_path)
        text = " ".join(seg.text for seg in segments).strip()
        return text or None
    except Exception:
        # Any transcription failure -> let the caller use x-vector-only mode.
        return None


@register_engine
class QwenVoiceCloneEngine(QwenEngineBase):
    """Qwen3-TTS voice clone — copy a voice from a reference clip, requires GPU.

    Developer-only engine (separate ~4 GB Base checkpoint). Run from source and
    ``pip install qwen-tts`` (plus ``faster-whisper`` for auto-transcribe).
    """

    id = "qwen_clone"
    display_name = "Qwen3-TTS Voice Clone (clone from a sample, requires GPU)"
    description = (
        "Local neural TTS that clones a voice from a short reference clip "
        "(pass a reference WAV). 10 languages (no Finnish). Requires an NVIDIA "
        "GPU and a manual `pip install qwen-tts`; `faster-whisper` enables the "
        "best-quality auto-transcribe path."
    )
    supports_voice_cloning = True
    # No free-text style control on the Base clone path (generate_voice_clone
    # takes no instruct); the voice comes entirely from the reference clip.
    supports_voice_description = False

    MODEL_ID = _HF_MODEL_ID
    _LABEL = "Qwen3-TTS Voice Clone"
    _TMP_PREFIX = "qwen_clone_"

    # --- voices --------------------------------------------------------- #
    # Cloning has no fixed catalogue: the "voice" is whatever reference clip the
    # caller supplies. So no preset voices and no default voice.

    def list_voices(self, language: str) -> list[Voice]:
        return []

    def default_voice(self, language: str) -> Optional[str]:
        return None

    # --- per-mode hooks ------------------------------------------------- #

    def _resolve_voice(self, voice_id: str, language: str) -> str:
        # There is no voice catalogue to resolve; the reference clip is the
        # voice. voice_id is unused — validation of the clip happens in
        # _prepare_generation (which receives reference_audio).
        return ""

    def _prepare_generation(
        self,
        voice_id: str,
        voice_description: Optional[str],
        reference_audio: Optional[str] = None,
    ) -> dict:
        if not reference_audio:
            raise ValueError(
                "Qwen3-TTS Voice Clone requires a reference audio clip "
                "(pass --ref-audio / a reference WAV)."
            )
        ref_path = os.path.expanduser(str(reference_audio))
        if not os.path.isfile(ref_path):
            raise ValueError(f"Reference audio not found: {reference_audio}")

        # ref_text priority: explicit override -> Whisper -> x-vector fallback.
        ref_text = os.environ.get(_REF_TEXT_ENV) or None
        if ref_text is not None and not ref_text.strip():
            ref_text = None
        if ref_text is None:
            ref_text = _transcribe_reference(ref_path)

        return {
            "ref_audio": ref_path,
            "ref_text": ref_text,
            # No transcript available -> let the model run in x-vector-only mode.
            "x_vector_only": ref_text is None,
        }

    def _generate(self, model, chunk: str, qwen_language: str, prepared: dict):
        kwargs = {
            "text": chunk,
            "language": qwen_language,
            "ref_audio": prepared["ref_audio"],
        }
        if prepared["x_vector_only"]:
            kwargs["x_vector_only_mode"] = True
        else:
            kwargs["ref_text"] = prepared["ref_text"]
        return model.generate_voice_clone(**kwargs)
