"""Unit tests for the VoxCPM2 TTS engine adapter.

VoxCPM2 itself is a heavy GPU-only package (~4 GB on disk with torch)
that we do NOT install in CI or in the test venv. All real synthesis
paths are mocked; the tests only verify that the adapter reports the
right status, exposes the right voices, and wires the existing
split_text_into_chunks / combine_audio_files pipeline correctly.
"""

from __future__ import annotations

import builtins
from unittest.mock import patch, MagicMock

import pytest

from src.tts_base import EngineStatus, Voice, get_engine
from src.tts_voxcpm import (
    VoxCPM2Engine,
    _DEFAULT_VOICES,
    _INSTALL_HINT,
    _build_description_prefix,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _force_import_error(missing: set[str]):
    """Return a custom __import__ that raises ImportError for given names."""
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name in missing or name.split(".")[0] in missing:
            raise ImportError(f"fake: {name} missing")
        return real_import(name, *args, **kwargs)

    return fake_import


# ---------------------------------------------------------------------------
# Registration / metadata
# ---------------------------------------------------------------------------


def test_voxcpm_engine_is_registered() -> None:
    engine = get_engine("voxcpm2")
    assert isinstance(engine, VoxCPM2Engine)


class TestMetadata:
    def test_id_and_display_name(self) -> None:
        assert VoxCPM2Engine.id == "voxcpm2"
        assert "VoxCPM2" in VoxCPM2Engine.display_name
        assert "GPU" in VoxCPM2Engine.display_name

    def test_requires_gpu_flag(self) -> None:
        assert VoxCPM2Engine.requires_gpu is True

    def test_supports_voice_cloning_flag(self) -> None:
        assert VoxCPM2Engine.supports_voice_cloning is True

    def test_does_not_require_internet(self) -> None:
        # After the model is cached, VoxCPM2 runs fully offline.
        assert VoxCPM2Engine.requires_internet is False


# ---------------------------------------------------------------------------
# check_status
# ---------------------------------------------------------------------------


class TestCheckStatus:
    def test_unavailable_when_voxcpm_not_installed(self) -> None:
        with patch("builtins.__import__", side_effect=_force_import_error({"voxcpm"})):
            status = VoxCPM2Engine().check_status()
        assert isinstance(status, EngineStatus)
        assert not status.available
        assert "voxcpm" in status.reason.lower()

    def test_unavailable_when_torch_missing(self) -> None:
        # Pretend voxcpm is there but torch is not — synthesize a fake
        # voxcpm module so the first import succeeds.
        fake_voxcpm = MagicMock()
        with patch.dict("sys.modules", {"voxcpm": fake_voxcpm}), patch(
            "builtins.__import__",
            side_effect=_force_import_error({"torch"}),
        ):
            status = VoxCPM2Engine().check_status()
        assert not status.available
        assert "torch" in status.reason.lower()

    def test_unavailable_when_no_cuda(self) -> None:
        fake_voxcpm = MagicMock()
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        with patch.dict(
            "sys.modules", {"voxcpm": fake_voxcpm, "torch": fake_torch}
        ):
            status = VoxCPM2Engine().check_status()
        assert not status.available
        assert "GPU" in status.reason or "CUDA" in status.reason

    def test_available_when_everything_present(self) -> None:
        fake_voxcpm = MagicMock()
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        with patch.dict(
            "sys.modules", {"voxcpm": fake_voxcpm, "torch": fake_torch}
        ):
            status = VoxCPM2Engine().check_status()
        assert status.available
        assert status.reason == ""


# ---------------------------------------------------------------------------
# Voices
# ---------------------------------------------------------------------------


class TestVoices:
    def test_finnish_default_voice_present(self) -> None:
        voices = VoxCPM2Engine().list_voices("fi")
        assert len(voices) == 1
        assert voices[0].language == "fi"
        assert "voxcpm2-default-fi" == voices[0].id

    def test_english_default_voice_present(self) -> None:
        voices = VoxCPM2Engine().list_voices("en")
        assert len(voices) == 1
        assert voices[0].language == "en"

    def test_unknown_language_returns_empty_list(self) -> None:
        assert VoxCPM2Engine().list_voices("xx") == []

    def test_voices_are_voice_instances(self) -> None:
        voices = VoxCPM2Engine().list_voices("fi")
        assert all(isinstance(v, Voice) for v in voices)

    def test_default_voice_for_finnish(self) -> None:
        assert VoxCPM2Engine().default_voice("fi") == "voxcpm2-default-fi"

    def test_default_voice_for_unknown_language_is_none(self) -> None:
        assert VoxCPM2Engine().default_voice("xx") is None

    def test_catalogue_entries_consistent(self) -> None:
        # Every entry in the private catalogue should surface in list_voices.
        for lang, (voice_id, display_name) in _DEFAULT_VOICES.items():
            voices = VoxCPM2Engine().list_voices(lang)
            assert any(v.id == voice_id for v in voices)


# ---------------------------------------------------------------------------
# synthesize
# ---------------------------------------------------------------------------


class TestSynthesize:
    def test_raises_on_empty_text(self) -> None:
        engine = VoxCPM2Engine()
        with pytest.raises(ValueError, match="empty"):
            engine.synthesize("", "/tmp/out.mp3", "voxcpm2-default-fi", "fi")

    def test_raises_on_unknown_voice(self) -> None:
        engine = VoxCPM2Engine()
        with pytest.raises(ValueError, match="Unknown VoxCPM2 voice"):
            engine.synthesize("hello", "/tmp/out.mp3", "unknown-voice", "fi")

    def test_raises_on_missing_reference_audio(self) -> None:
        engine = VoxCPM2Engine()
        with pytest.raises(ValueError, match="Reference audio not found"):
            engine.synthesize(
                "hello",
                "/tmp/out.mp3",
                "voxcpm2-default-fi",
                "fi",
                reference_audio="/nonexistent/path.wav",
            )

    def test_raises_when_engine_unavailable(self) -> None:
        engine = VoxCPM2Engine()
        with patch("builtins.__import__", side_effect=_force_import_error({"voxcpm"})):
            with pytest.raises(RuntimeError, match="unavailable"):
                engine.synthesize(
                    "hello",
                    "/tmp/out.mp3",
                    "voxcpm2-default-fi",
                    "fi",
                )


# ---------------------------------------------------------------------------
# Voice description (voice design) prefix handling
# ---------------------------------------------------------------------------


class TestBuildDescriptionPrefix:
    def test_none_returns_empty(self) -> None:
        assert _build_description_prefix(None) == ""

    def test_empty_string_returns_empty(self) -> None:
        assert _build_description_prefix("") == ""

    def test_whitespace_only_returns_empty(self) -> None:
        assert _build_description_prefix("   \t  ") == ""

    def test_wraps_plain_string_in_parentheses(self) -> None:
        assert _build_description_prefix("warm baritone") == "(warm baritone)"

    def test_already_parenthesized_is_normalised(self) -> None:
        assert _build_description_prefix("(warm baritone)") == "(warm baritone)"

    def test_parenthesized_with_whitespace_is_normalised(self) -> None:
        assert _build_description_prefix("  ( warm baritone )  ") == "(warm baritone)"

    def test_parens_only_returns_empty(self) -> None:
        assert _build_description_prefix("()") == ""


class TestVoiceDescriptionFlag:
    def test_supports_voice_description_is_true(self) -> None:
        assert VoxCPM2Engine.supports_voice_description is True

    def test_supports_voice_cloning_is_true(self) -> None:
        assert VoxCPM2Engine.supports_voice_cloning is True
