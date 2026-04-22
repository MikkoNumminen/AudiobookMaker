"""Unit tests for the Piper TTS engine adapter.

Network downloads and the piper runtime are mocked. A single integration
test runs an end-to-end synthesis if a voice is already cached on the
developer's machine.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.tts_base import EngineStatus, Voice, get_engine
from src.tts_piper import (
    PiperTTSEngine,
    _PIPER_VOICES,
    _VOICES_BY_ID,
    _cache_dir,
    _is_voice_cached,
    download_voice,
)


# Detect whether piper-tts is installed so we can mark integration tests.
try:
    import piper  # noqa: F401

    PIPER_INSTALLED = True
except ImportError:
    PIPER_INSTALLED = False


# Detect whether ffmpeg is available (required for combine_audio_files).
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


# ---------------------------------------------------------------------------
# Registration / metadata
# ---------------------------------------------------------------------------


def test_piper_engine_is_registered() -> None:
    engine = get_engine("piper")
    assert isinstance(engine, PiperTTSEngine)


class TestMetadata:
    def test_id_and_display_name(self) -> None:
        assert PiperTTSEngine.id == "piper"
        assert "Piper" in PiperTTSEngine.display_name

    def test_does_not_require_gpu(self) -> None:
        assert PiperTTSEngine.requires_gpu is False

    def test_does_not_require_internet(self) -> None:
        # Offline once voices are downloaded.
        assert PiperTTSEngine.requires_internet is False

    def test_does_not_support_cloning(self) -> None:
        assert PiperTTSEngine.supports_voice_cloning is False

    def test_does_not_support_voice_description(self) -> None:
        assert PiperTTSEngine.supports_voice_description is False


# ---------------------------------------------------------------------------
# Voice catalogue
# ---------------------------------------------------------------------------


class TestVoices:
    def test_finnish_voice_present(self) -> None:
        voices = PiperTTSEngine().list_voices("fi")
        ids = {v.id for v in voices}
        assert "fi_FI-harri-medium" in ids

    def test_english_voices_present(self) -> None:
        voices = PiperTTSEngine().list_voices("en")
        ids = {v.id for v in voices}
        assert len(ids) >= 1

    def test_voices_are_voice_instances(self) -> None:
        voices = PiperTTSEngine().list_voices("fi")
        assert all(isinstance(v, Voice) for v in voices)
        assert all(v.language == "fi" for v in voices)

    def test_unknown_language_returns_empty_list(self) -> None:
        assert PiperTTSEngine().list_voices("xx") == []

    def test_default_voice_for_finnish(self) -> None:
        assert PiperTTSEngine().default_voice("fi") == "fi_FI-harri-medium"

    def test_default_voice_for_english_is_a_known_id(self) -> None:
        default = PiperTTSEngine().default_voice("en")
        assert default is not None
        assert default in _VOICES_BY_ID

    def test_default_voice_unknown_language_is_none(self) -> None:
        assert PiperTTSEngine().default_voice("xx") is None


# ---------------------------------------------------------------------------
# check_status
# ---------------------------------------------------------------------------


class TestCheckStatus:
    def test_unavailable_when_piper_not_installed(self) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args, **kwargs):
            if name == "piper":
                raise ImportError("piper missing")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            status = PiperTTSEngine().check_status()
        assert isinstance(status, EngineStatus)
        assert not status.available
        assert "piper-tts" in status.reason

    @pytest.mark.skipif(not PIPER_INSTALLED, reason="piper-tts not installed")
    def test_needs_download_when_no_voices_cached(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("src.tts_piper._cache_dir", lambda: tmp_path)
        status = PiperTTSEngine().check_status()
        assert status.available
        assert status.needs_download

    @pytest.mark.skipif(not PIPER_INSTALLED, reason="piper-tts not installed")
    def test_ready_when_voice_cached(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("src.tts_piper._cache_dir", lambda: tmp_path)
        # Fake-cache the first voice.
        spec = _PIPER_VOICES[0]
        (tmp_path / spec.onnx_filename).write_bytes(b"fake")
        (tmp_path / spec.json_filename).write_bytes(b"{}")
        status = PiperTTSEngine().check_status()
        assert status.available
        assert not status.needs_download


# ---------------------------------------------------------------------------
# download_voice
# ---------------------------------------------------------------------------


class TestDownloadVoice:
    def test_unknown_voice_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown"):
            download_voice("nope")

    def test_download_file_passes_timeout_to_urlopen(
        self, tmp_path, monkeypatch
    ) -> None:
        """Voice downloads must bound urlopen with a timeout.

        Without this, a slow/dead HuggingFace CDN endpoint could hang the
        GUI thread that prompted the download for an unbounded amount of
        time. The exact value is a tuning knob — we only assert that a
        positive timeout is passed.
        """
        from src.tts_piper import _download_file

        captured: dict[str, object] = {}

        class _FakeResp:
            headers = {"Content-Length": "0"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, _size):
                return b""

        def fake_urlopen(req, **kwargs):
            captured.update(kwargs)
            return _FakeResp()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        dest = tmp_path / "voice.onnx"
        _download_file("https://example.com/voice.onnx", dest, None, "label")

        assert "timeout" in captured
        assert isinstance(captured["timeout"], (int, float))
        assert captured["timeout"] > 0

    def test_noop_when_already_cached(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("src.tts_piper._cache_dir", lambda: tmp_path)
        spec = _PIPER_VOICES[0]
        (tmp_path / spec.onnx_filename).write_bytes(b"fake")
        (tmp_path / spec.json_filename).write_bytes(b"{}")

        with patch("src.tts_piper._download_file", autospec=True) as mock_dl:
            download_voice(spec.id)
        mock_dl.assert_not_called()

    def test_downloads_both_files_when_missing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("src.tts_piper._cache_dir", lambda: tmp_path)
        spec = _PIPER_VOICES[0]
        calls: list[str] = []

        def fake_download(url: str, dest: Path, progress_cb, label: str) -> None:
            dest.write_bytes(b"fake")
            calls.append(url)

        monkeypatch.setattr("src.tts_piper._download_file", fake_download)
        download_voice(spec.id)
        assert len(calls) == 2
        # Both .onnx and .onnx.json should have been downloaded.
        assert any("onnx.json" in u for u in calls)
        assert any(u.endswith(".onnx") for u in calls)


# ---------------------------------------------------------------------------
# synthesize
# ---------------------------------------------------------------------------


class TestSynthesize:
    def test_raises_on_empty_text(self) -> None:
        engine = PiperTTSEngine()
        with pytest.raises(ValueError, match="empty"):
            engine.synthesize("", "/tmp/out.mp3", "fi_FI-harri-medium", "fi")

    def test_raises_on_unknown_voice(self) -> None:
        engine = PiperTTSEngine()
        with pytest.raises(ValueError, match="Unknown Piper voice"):
            engine.synthesize("hello", "/tmp/out.mp3", "unknown-voice", "fi")

    @pytest.mark.skipif(
        not (PIPER_INSTALLED and FFMPEG_AVAILABLE),
        reason="integration test needs piper-tts and ffmpeg",
    )
    def test_end_to_end_with_cached_voice(self, tmp_path) -> None:
        """Real synthesis against a real cached model, if one is available."""
        spec = next(
            (s for s in _PIPER_VOICES if _is_voice_cached(s)),
            None,
        )
        if spec is None:
            pytest.skip("No Piper voice cached on this machine — run tts_piper manually once to populate the cache.")

        out = tmp_path / "out.mp3"
        engine = PiperTTSEngine()
        engine.synthesize(
            "Tämä on testi.",
            str(out),
            spec.id,
            spec.language,
        )
        assert out.exists()
        assert out.stat().st_size > 1000  # non-trivial audio file


# ---------------------------------------------------------------------------
# Cache dir
# ---------------------------------------------------------------------------


def test_cache_dir_is_created() -> None:
    path = _cache_dir()
    assert path.exists()
    assert path.is_dir()


# ---------------------------------------------------------------------------
# supported_languages
# ---------------------------------------------------------------------------


class TestSupportedLanguages:
    def test_returns_fi_and_en(self) -> None:
        # Piper's catalogue has de voices too, but we only expose fi/en
        # in the UI per Phase 2 design.
        assert PiperTTSEngine().supported_languages() == {"fi", "en"}

    def test_returns_a_set(self) -> None:
        assert isinstance(PiperTTSEngine().supported_languages(), set)
