"""Edge-TTS engine adapter.

Wraps the existing low-level `src.tts_engine` pipeline (text chunking,
edge-tts synthesis, pydub combine) in the new `TTSEngine` interface.
This module is intentionally thin — all the actual TTS logic still
lives in `src/tts_engine.py`.
"""

from __future__ import annotations

from typing import Optional

from src.tts_base import (
    EngineStatus,
    ProgressCallback,
    TTSEngine,
    Voice,
    register_engine,
)
from src.tts_engine import (
    VOICES as _EDGE_VOICES,
    VOICE_DISPLAY_NAMES as _EDGE_VOICE_NAMES,
    TTSConfig,
    text_to_speech as _edge_text_to_speech,
)


@register_engine
class EdgeTTSEngine(TTSEngine):
    """Microsoft Edge online TTS.

    Uses the free edge-tts Python client to hit Microsoft's speech
    synthesis endpoint. Requires an internet connection but produces
    high quality Finnish and English speech with no local model
    downloads.
    """

    id = "edge"
    display_name = "Edge-TTS (online, no GPU needed)"
    description = (
        "Microsoft's online neural voices. Fast, free, high quality. "
        "Requires an internet connection."
    )
    requires_gpu = False
    requires_internet = True
    supports_voice_cloning = False

    # --------------------------------------------------------------------- #
    # Status
    # --------------------------------------------------------------------- #

    def check_status(self) -> EngineStatus:
        try:
            import edge_tts  # noqa: F401  (import check only)
        except ImportError:
            return EngineStatus(
                available=False,
                reason="Install required: pip install edge-tts",
            )
        return EngineStatus(available=True)

    # --------------------------------------------------------------------- #
    # Voices
    # --------------------------------------------------------------------- #

    def list_voices(self, language: str) -> list[Voice]:
        lang_voices = _EDGE_VOICES.get(language, {})
        result: list[Voice] = []
        for display_name, voice_id in lang_voices.items():
            if display_name == "default":
                continue
            # Gender is encoded in the display name text (heuristic fallback).
            gender = ""
            lower = display_name.lower()
            if any(word in lower for word in ("female", "nainen")):
                gender = "female"
            elif any(word in lower for word in ("male", "mies")):
                gender = "male"
            result.append(
                Voice(
                    id=voice_id,
                    display_name=display_name,
                    language=language,
                    gender=gender,
                )
            )
        return result

    def default_voice(self, language: str) -> Optional[str]:
        lang_voices = _EDGE_VOICES.get(language)
        if not lang_voices:
            # Fall back to Finnish default if the requested language
            # has no configured voices.
            return _EDGE_VOICES.get("fi", {}).get("default")
        return lang_voices.get("default")

    # --------------------------------------------------------------------- #
    # Synthesis
    # --------------------------------------------------------------------- #

    def synthesize(
        self,
        text: str,
        output_path: str,
        voice_id: str,
        language: str,
        progress_cb: Optional[ProgressCallback] = None,
        reference_audio: Optional[str] = None,
    ) -> None:
        # Edge-TTS does not support voice cloning; silently ignore reference.
        if not text or not text.strip():
            raise ValueError("Cannot synthesize empty text.")
        if not voice_id:
            voice_id = self.default_voice(language) or ""
            if not voice_id:
                raise ValueError(f"No Edge-TTS voice available for language '{language}'")

        config = TTSConfig(language=language, voice=voice_id, rate="+0%")
        _edge_text_to_speech(text, output_path, config, progress_cb)
