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


class TestSupportedLanguages:
    def test_returns_fi_and_en(self) -> None:
        assert VoxCPM2Engine().supported_languages() == {"fi", "en"}

    def test_returns_a_set(self) -> None:
        assert isinstance(VoxCPM2Engine().supported_languages(), set)


# ---------------------------------------------------------------------------
# Sample rate loading contract
# ---------------------------------------------------------------------------


class TestSampleRateContract:
    """``_sample_rate`` must be populated by ``_load_model`` before
    synthesize() writes any chunk. If it is still ``None`` we must fail
    loud — the previous silent ``or 24000`` fallback would have written
    audio at the wrong playback speed whenever the model exposed its
    sample rate through a different attribute."""

    def test_synthesize_raises_when_sample_rate_not_loaded(
        self, tmp_path, monkeypatch
    ) -> None:
        """Simulate a broken _load_model that forgets to set _sample_rate."""
        fake_voxcpm = MagicMock()
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_sf = MagicMock()

        engine = VoxCPM2Engine()

        # Replace _load_model with one that populates the model cache but
        # leaves _sample_rate at None — the exact failure mode the guard
        # is there to catch.
        def broken_load_model():
            engine._model = MagicMock()
            # Intentionally do NOT set engine._sample_rate.
            return engine._model

        engine._load_model = broken_load_model  # type: ignore[method-assign]

        out_path = tmp_path / "out.mp3"
        with patch.dict(
            "sys.modules",
            {"voxcpm": fake_voxcpm, "torch": fake_torch, "soundfile": fake_sf},
        ):
            with pytest.raises(RuntimeError, match="sample rate"):
                engine.synthesize(
                    "hello world",
                    str(out_path),
                    "voxcpm2-default-fi",
                    "fi",
                )

    def test_synthesize_uses_loaded_sample_rate_not_fallback(
        self, tmp_path
    ) -> None:
        """When _load_model populates _sample_rate, that value is what
        reaches soundfile.write — not a hard-coded 24 kHz default."""
        fake_voxcpm = MagicMock()
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True

        captured_rates: list[int] = []

        def fake_write(path, wav, rate):
            captured_rates.append(rate)
            # Touch the path so combine_audio_files has something to read
            # if it gets that far.
            import os as _os
            with open(path, "wb") as _fh:
                _fh.write(b"")
            _ = _os  # quiet lint

        fake_sf = MagicMock()
        fake_sf.write = fake_write

        engine = VoxCPM2Engine()

        def good_load_model():
            engine._model = MagicMock()
            # The real model reports 16000 in this hypothetical run; make
            # sure THAT is what reaches soundfile.write, not 24000.
            engine._sample_rate = 16000
            engine._model.generate.return_value = [0.0, 0.1, 0.0]
            return engine._model

        engine._load_model = good_load_model  # type: ignore[method-assign]

        # Short-circuit combine_audio_files so the test does not need ffmpeg.
        out_path = tmp_path / "out.mp3"
        with patch.dict(
            "sys.modules",
            {"voxcpm": fake_voxcpm, "torch": fake_torch, "soundfile": fake_sf},
        ), patch("src.tts_voxcpm.combine_audio_files") as fake_combine:
            engine.synthesize(
                "hello world",
                str(out_path),
                "voxcpm2-default-fi",
                "fi",
            )
            assert fake_combine.called

        # Every soundfile.write call for every chunk should have used the
        # model-reported 16000, never the old 24000 fallback.
        assert captured_rates, "expected at least one chunk to be written"
        assert all(r == 16000 for r in captured_rates), captured_rates
