"""Unit tests for the Edge-TTS engine adapter.

Network calls are mocked — we only verify the adapter correctly wires
the new TTSEngine interface into the existing edge-tts pipeline.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.tts_base import EngineStatus, Voice, get_engine
from src.tts_edge import EdgeTTSEngine


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_edge_engine_is_registered() -> None:
    """Importing tts_edge must register the engine under id 'edge'."""
    engine = get_engine("edge")
    assert isinstance(engine, EdgeTTSEngine)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_id_and_display_name(self) -> None:
        assert EdgeTTSEngine.id == "edge"
        assert "Edge" in EdgeTTSEngine.display_name

    def test_does_not_require_gpu(self) -> None:
        assert EdgeTTSEngine.requires_gpu is False

    def test_requires_internet(self) -> None:
        assert EdgeTTSEngine.requires_internet is True

    def test_does_not_support_cloning(self) -> None:
        assert EdgeTTSEngine.supports_voice_cloning is False


# ---------------------------------------------------------------------------
# check_status
# ---------------------------------------------------------------------------


class TestCheckStatus:
    def test_available_when_edge_tts_importable(self) -> None:
        status = EdgeTTSEngine().check_status()
        assert isinstance(status, EngineStatus)
        assert status.available is True

    def test_unavailable_when_import_fails(self) -> None:
        # Force the import inside check_status to fail.
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args, **kwargs):
            if name == "edge_tts":
                raise ImportError("edge_tts missing")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            status = EdgeTTSEngine().check_status()
        assert not status.available
        assert "edge-tts" in status.reason


# ---------------------------------------------------------------------------
# list_voices / default_voice
# ---------------------------------------------------------------------------


class TestVoices:
    def test_finnish_voices_present(self) -> None:
        voices = EdgeTTSEngine().list_voices("fi")
        ids = {v.id for v in voices}
        assert "fi-FI-NooraNeural" in ids
        assert "fi-FI-HarriNeural" in ids

    def test_voices_are_voice_instances(self) -> None:
        voices = EdgeTTSEngine().list_voices("fi")
        assert all(isinstance(v, Voice) for v in voices)
        assert all(v.language == "fi" for v in voices)

    def test_gender_inferred_from_display_name(self) -> None:
        voices = {v.id: v for v in EdgeTTSEngine().list_voices("fi")}
        assert voices["fi-FI-NooraNeural"].gender == "female"
        assert voices["fi-FI-HarriNeural"].gender == "male"

    def test_english_voices_present(self) -> None:
        voices = EdgeTTSEngine().list_voices("en")
        ids = {v.id for v in voices}
        assert "en-US-JennyNeural" in ids

    def test_unknown_language_returns_empty_list(self) -> None:
        assert EdgeTTSEngine().list_voices("xx") == []

    def test_default_voice_for_finnish(self) -> None:
        assert EdgeTTSEngine().default_voice("fi") == "fi-FI-NooraNeural"

    def test_default_voice_for_english(self) -> None:
        assert EdgeTTSEngine().default_voice("en") == "en-US-JennyNeural"

    def test_default_voice_unknown_language_falls_back_to_finnish(self) -> None:
        assert EdgeTTSEngine().default_voice("xx") == "fi-FI-NooraNeural"


# ---------------------------------------------------------------------------
# synthesize
# ---------------------------------------------------------------------------


class TestSynthesize:
    def test_raises_on_empty_text(self) -> None:
        engine = EdgeTTSEngine()
        with pytest.raises(ValueError, match="empty"):
            engine.synthesize("", "/tmp/out.mp3", "fi-FI-NooraNeural", "fi")

    def test_empty_voice_id_falls_back_to_default(self) -> None:
        engine = EdgeTTSEngine()
        with patch("src.tts_edge._edge_text_to_speech") as mock_tts:
            engine.synthesize("hello", "/tmp/out.mp3", "", "fi")
        assert mock_tts.called
        # The TTSConfig passed to text_to_speech should have the default voice
        args, kwargs = mock_tts.call_args
        config = args[2]
        assert config.voice == "fi-FI-NooraNeural"

    def test_delegates_to_text_to_speech(self) -> None:
        engine = EdgeTTSEngine()
        with patch("src.tts_edge._edge_text_to_speech") as mock_tts:
            engine.synthesize(
                "hello world", "/tmp/x.mp3", "fi-FI-HarriNeural", "fi"
            )
        assert mock_tts.call_count == 1
        args, _ = mock_tts.call_args
        text, out, config, _ = args
        assert text == "hello world"
        assert out == "/tmp/x.mp3"
        assert config.voice == "fi-FI-HarriNeural"
        assert config.language == "fi"

    def test_reference_audio_is_ignored(self) -> None:
        # Edge-TTS does not clone; passing reference_audio must not crash.
        engine = EdgeTTSEngine()
        with patch("src.tts_edge._edge_text_to_speech"):
            engine.synthesize(
                "hello",
                "/tmp/x.mp3",
                "fi-FI-NooraNeural",
                "fi",
                reference_audio="/path/to/ref.wav",
            )
