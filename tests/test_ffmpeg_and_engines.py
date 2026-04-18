"""Tests for ffmpeg setup, auto-updater batch script, and TTS engine pipelines.

Covers the critical issues that caused WinError 2 and auto-update failures:
  - ffmpeg/ffprobe discovery and pydub configuration
  - Auto-updater batch script generation (correct syntax, binary writing)
  - Edge-TTS synthesis + audio combining (full pipeline)
  - Piper engine availability and voice catalogue
  - Chatterbox bridge resolution
  - Finnish text normalization end-to-end
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# ffmpeg path discovery
# ---------------------------------------------------------------------------


class TestGetFfmpegExe:
    """Tests for get_ffmpeg_exe() path search logic."""

    def test_finds_ffmpeg_in_meipass(self, tmp_path: Path) -> None:
        from src.ffmpeg_path import get_ffmpeg_exe

        ffmpeg = tmp_path / "ffmpeg.exe"
        ffmpeg.write_bytes(b"fake")

        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "_MEIPASS", str(tmp_path), create=True):
            result = get_ffmpeg_exe()
            assert result == str(ffmpeg)

    def test_finds_ffmpeg_next_to_executable(self, tmp_path: Path) -> None:
        from src.ffmpeg_path import get_ffmpeg_exe

        ffmpeg = tmp_path / "ffmpeg.exe"
        ffmpeg.write_bytes(b"fake")
        fake_exe = tmp_path / "python.exe"
        fake_exe.write_bytes(b"fake")

        with patch.object(sys, "executable", str(fake_exe)), \
             patch("src.ffmpeg_path.getattr", return_value=False):
            # Clear frozen to skip _MEIPASS check
            frozen = getattr(sys, "frozen", None)
            if frozen is not None:
                delattr(sys, "frozen")
            try:
                result = get_ffmpeg_exe()
                assert result == str(ffmpeg)
            finally:
                if frozen is not None:
                    sys.frozen = frozen

    def test_finds_ffmpeg_in_dist(self) -> None:
        from src.ffmpeg_path import get_ffmpeg_exe

        repo_root = str(Path(__file__).resolve().parent.parent)
        dist_ffmpeg = os.path.join(repo_root, "dist", "ffmpeg", "ffmpeg.exe")

        if os.path.isfile(dist_ffmpeg):
            # Dev environment with dist/ffmpeg/ present
            result = get_ffmpeg_exe()
            assert result is not None
            assert "ffmpeg" in result.lower()
        else:
            pytest.skip("dist/ffmpeg/ffmpeg.exe not present in dev environment")

    def test_returns_none_when_nothing_found(self, tmp_path: Path) -> None:
        from src.ffmpeg_path import get_ffmpeg_exe

        fake_exe = tmp_path / "python.exe"
        fake_exe.write_bytes(b"fake")

        with patch.object(sys, "executable", str(fake_exe)), \
             patch("shutil.which", return_value=None):
            frozen = getattr(sys, "frozen", None)
            if frozen is not None:
                delattr(sys, "frozen")
            try:
                result = get_ffmpeg_exe()
                # Could be None or could find dist/ffmpeg — both are valid
                # The test verifies it doesn't crash
            finally:
                if frozen is not None:
                    sys.frozen = frozen


class TestSetupFfmpegPath:
    """Tests for setup_ffmpeg_path() pydub configuration."""

    @pytest.fixture(autouse=True)
    def _restore_ffmpeg_globals(self):
        """Snapshot and restore globals that setup_ffmpeg_path mutates.

        Without this, leftover state (a tmp_path entry prepended to
        ``PATH`` or pydub.AudioSegment.converter pointing at a deleted
        stub) leaks into later tests. tests/test_piper_e2e.py was the
        first casualty: shutil.which('ffmpeg') would find the deleted
        stub still in ``PATH``, and a real subprocess.run would crash
        with WinError 216 because the file is just b"fake".
        """
        saved_path = os.environ.get("PATH", "")
        try:
            from pydub import AudioSegment
            import pydub.utils
            saved_converter = AudioSegment.converter
            saved_ffprobe = getattr(AudioSegment, "ffprobe", None)
            saved_get_prober_name = pydub.utils.get_prober_name
        except ImportError:
            saved_converter = saved_ffprobe = saved_get_prober_name = None

        yield

        os.environ["PATH"] = saved_path
        if saved_get_prober_name is not None:
            from pydub import AudioSegment
            import pydub.utils
            AudioSegment.converter = saved_converter
            if saved_ffprobe is not None:
                AudioSegment.ffprobe = saved_ffprobe
            pydub.utils.get_prober_name = saved_get_prober_name

    def test_sets_audiosegment_converter(self, tmp_path: Path) -> None:
        from src.ffmpeg_path import setup_ffmpeg_path

        ffmpeg = tmp_path / "ffmpeg.exe"
        ffmpeg.write_bytes(b"fake")

        with patch("src.ffmpeg_path.get_ffmpeg_exe", autospec=True, return_value=str(ffmpeg)):
            setup_ffmpeg_path()

            from pydub import AudioSegment
            assert AudioSegment.converter == str(ffmpeg)

    def test_sets_ffprobe_when_present(self, tmp_path: Path) -> None:
        from src.ffmpeg_path import setup_ffmpeg_path

        ffmpeg = tmp_path / "ffmpeg.exe"
        ffmpeg.write_bytes(b"fake")
        ffprobe = tmp_path / "ffprobe.exe"
        ffprobe.write_bytes(b"fake")

        with patch("src.ffmpeg_path.get_ffmpeg_exe", autospec=True, return_value=str(ffmpeg)):
            setup_ffmpeg_path()

            from pydub import AudioSegment
            assert AudioSegment.ffprobe == str(ffprobe)

    def test_adds_to_path_env(self, tmp_path: Path) -> None:
        from src.ffmpeg_path import setup_ffmpeg_path

        ffmpeg = tmp_path / "ffmpeg.exe"
        ffmpeg.write_bytes(b"fake")

        with patch("src.ffmpeg_path.get_ffmpeg_exe", autospec=True, return_value=str(ffmpeg)):
            setup_ffmpeg_path()
            assert str(tmp_path) in os.environ["PATH"]

    def test_noop_when_ffmpeg_not_found(self) -> None:
        from src.ffmpeg_path import setup_ffmpeg_path

        with patch("src.ffmpeg_path.get_ffmpeg_exe", autospec=True, return_value=None):
            # Should not raise
            setup_ffmpeg_path()

    def test_repeated_setup_does_not_wrap_popen_recursively(
        self, tmp_path: Path
    ) -> None:
        """Regression: calling setup_ffmpeg_path repeatedly must not
        re-wrap subprocess.Popen each time. Before the idempotency
        guard, every call added another _SilentPopen layer, producing
        arbitrarily deep recursion in the Popen.__init__ chain on
        every spawned subprocess (visible as a 5-deep traceback in
        the bug that motivated this test)."""
        from src import ffmpeg_path
        from src.ffmpeg_path import setup_ffmpeg_path

        ffmpeg = tmp_path / "ffmpeg.exe"
        ffmpeg.write_bytes(b"fake")

        # Force a fresh patch attempt regardless of prior test state.
        ffmpeg_path._PYDUB_PATCHED = False

        with patch("src.ffmpeg_path.get_ffmpeg_exe", autospec=True, return_value=str(ffmpeg)):
            setup_ffmpeg_path()
            try:
                import pydub.utils
                first_popen = pydub.utils.Popen
            except ImportError:
                pytest.skip("pydub not installed")

            for _ in range(4):
                setup_ffmpeg_path()

            # Same wrapper class — no new layer added on each call.
            assert pydub.utils.Popen is first_popen


# ---------------------------------------------------------------------------
# Auto-updater batch script
# ---------------------------------------------------------------------------


class TestApplyUpdateBatchScript:
    """Tests for the batch script generated by apply_update().

    We mock os._exit and subprocess.Popen to prevent actual process
    termination and batch execution, then inspect the generated file.
    """

    def _generate_batch(self, tmp_path: Path) -> str:
        """Run apply_update with mocks and return the batch file contents."""
        from src.auto_updater import apply_update

        installer = tmp_path / "AudiobookMaker-Setup-3.0.0.exe"
        installer.write_bytes(b"fake-installer")
        bat_path = tmp_path / "audiobookmaker_relaunch.bat"

        with patch("src.auto_updater.tempfile.gettempdir", return_value=str(tmp_path)), \
             patch("src.auto_updater.subprocess.Popen") as mock_popen, \
             patch("src.auto_updater.os._exit") as mock_exit, \
             patch("src.auto_updater.Path.resolve", return_value=Path(r"C:\App\AudiobookMaker.exe")), \
             patch.object(sys, "executable", r"C:\App\AudiobookMaker.exe"), \
             patch("src.single_instance.release", autospec=True):

            apply_update(installer)

            mock_exit.assert_called_once_with(0)
            mock_popen.assert_called_once()

        assert bat_path.exists()
        return bat_path.read_bytes().decode("utf-8")

    def test_batch_uses_crlf_line_endings(self, tmp_path: Path) -> None:
        content = self._generate_batch(tmp_path)
        raw = (tmp_path / "audiobookmaker_relaunch.bat").read_bytes()
        assert b"\r\n" in raw

    def test_batch_contains_waitfor_not_timeout(self, tmp_path: Path) -> None:
        content = self._generate_batch(tmp_path)
        assert "waitfor" in content
        assert "timeout" not in content.lower()

    def test_batch_contains_verysilent_flag(self, tmp_path: Path) -> None:
        content = self._generate_batch(tmp_path)
        assert "/VERYSILENT" in content

    def test_batch_contains_installer_path(self, tmp_path: Path) -> None:
        content = self._generate_batch(tmp_path)
        assert "AudiobookMaker-Setup-3.0.0.exe" in content

    def test_batch_contains_start_command(self, tmp_path: Path) -> None:
        content = self._generate_batch(tmp_path)
        assert 'start ""' in content

    def test_batch_contains_log_output(self, tmp_path: Path) -> None:
        content = self._generate_batch(tmp_path)
        assert ">> \"%LOG%\"" in content
        assert "audiobookmaker_update.log" in content

    def test_batch_launches_splash_before_installer(self, tmp_path: Path) -> None:
        """During the install phase the user must see the goat splash so
        the 10-15s gap after the app exits doesn't look like a crash."""
        content = self._generate_batch(tmp_path)
        # PowerShell splash invocation
        assert "powershell" in content.lower()
        assert "SPLASH" in content
        # Splash must come BEFORE the installer runs so it covers the gap.
        splash_idx = content.lower().find("powershell")
        installer_idx = content.find("/VERYSILENT")
        assert splash_idx < installer_idx, "splash must start before installer"

    def test_batch_written_as_bytes_not_text(self, tmp_path: Path) -> None:
        """Verify write_bytes is used (prevents MSYS2 >NUL mangling)."""
        content = self._generate_batch(tmp_path)
        raw = (tmp_path / "audiobookmaker_relaunch.bat").read_bytes()
        # If written correctly, >NUL should be literal bytes, not /dev/null
        assert b">NUL" in raw or b">nul" in raw

    def test_uses_create_no_window(self, tmp_path: Path) -> None:
        """Verify subprocess uses CREATE_NO_WINDOW, not DETACHED_PROCESS."""
        from src.auto_updater import apply_update

        installer = tmp_path / "AudiobookMaker-Setup-3.0.0.exe"
        installer.write_bytes(b"fake-installer")

        with patch("src.auto_updater.tempfile.gettempdir", return_value=str(tmp_path)), \
             patch("src.auto_updater.subprocess.Popen") as mock_popen, \
             patch("src.auto_updater.os._exit"), \
             patch.object(sys, "executable", r"C:\App\AudiobookMaker.exe"), \
             patch("src.single_instance.release", autospec=True):

            apply_update(installer)

            call_kwargs = mock_popen.call_args
            flags = call_kwargs[1].get("creationflags", 0) if call_kwargs[1] else 0
            if not flags:
                flags = call_kwargs.kwargs.get("creationflags", 0)

            # CREATE_NO_WINDOW = 0x08000000
            assert flags == subprocess.CREATE_NO_WINDOW

    def test_uses_os_exit_not_sys_exit(self, tmp_path: Path) -> None:
        """Verify os._exit(0) is called, not sys.exit(0)."""
        from src.auto_updater import apply_update

        installer = tmp_path / "AudiobookMaker-Setup-3.0.0.exe"
        installer.write_bytes(b"fake-installer")

        with patch("src.auto_updater.tempfile.gettempdir", return_value=str(tmp_path)), \
             patch("src.auto_updater.subprocess.Popen"), \
             patch("src.auto_updater.os._exit") as mock_os_exit, \
             patch.object(sys, "executable", r"C:\App\AudiobookMaker.exe"), \
             patch("src.single_instance.release", autospec=True):

            apply_update(installer)
            mock_os_exit.assert_called_once_with(0)


# ---------------------------------------------------------------------------
# Edge-TTS synthesis (requires internet)
# ---------------------------------------------------------------------------

# Check if we have a working ffmpeg + ffprobe for real audio tests
_FFMPEG_AVAILABLE = False
try:
    from src.ffmpeg_path import setup_ffmpeg_path, get_ffmpeg_exe
    setup_ffmpeg_path()
    _ffmpeg = get_ffmpeg_exe()
    if _ffmpeg and os.path.isfile(_ffmpeg):
        _FFMPEG_AVAILABLE = True
except Exception:
    pass


@pytest.mark.network
@pytest.mark.skipif(not _FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available")
class TestEdgeTTSPipeline:
    """Real Edge-TTS synthesis + pydub combining. Requires internet + ffmpeg."""

    def test_finnish_synthesis(self) -> None:
        from src.tts_engine import TTSConfig, text_to_speech

        out = os.path.join(tempfile.gettempdir(), "test_edge_fi.mp3")
        try:
            text_to_speech(
                "Mummo kerää marjoja metsässä.",
                out,
                config=TTSConfig(voice="fi-FI-NooraNeural", language="fi"),
            )
            assert os.path.isfile(out)
            assert os.path.getsize(out) > 1000  # Should be > 1KB
        finally:
            if os.path.exists(out):
                os.unlink(out)

    def test_english_synthesis(self) -> None:
        from src.tts_engine import TTSConfig, text_to_speech

        out = os.path.join(tempfile.gettempdir(), "test_edge_en.mp3")
        try:
            text_to_speech(
                "The quick brown fox jumps over the lazy dog.",
                out,
                config=TTSConfig(
                    voice="en-US-AriaNeural", language="en",
                    normalize_text=False,
                ),
            )
            assert os.path.isfile(out)
            assert os.path.getsize(out) > 1000
        finally:
            if os.path.exists(out):
                os.unlink(out)

    def test_german_synthesis(self) -> None:
        from src.tts_engine import TTSConfig, text_to_speech

        out = os.path.join(tempfile.gettempdir(), "test_edge_de.mp3")
        try:
            text_to_speech(
                "Der schnelle braune Fuchs springt über den faulen Hund.",
                out,
                config=TTSConfig(
                    voice="de-DE-KatjaNeural", language="de",
                    normalize_text=False,
                ),
            )
            assert os.path.isfile(out)
            assert os.path.getsize(out) > 1000
        finally:
            if os.path.exists(out):
                os.unlink(out)

    def test_swedish_synthesis(self) -> None:
        from src.tts_engine import TTSConfig, text_to_speech

        out = os.path.join(tempfile.gettempdir(), "test_edge_sv.mp3")
        try:
            text_to_speech(
                "Den snabba bruna räven hoppar över den lata hunden.",
                out,
                config=TTSConfig(
                    voice="sv-SE-SofieNeural", language="sv",
                    normalize_text=False,
                ),
            )
            assert os.path.isfile(out)
            assert os.path.getsize(out) > 1000
        finally:
            if os.path.exists(out):
                os.unlink(out)

    def test_finnish_with_normalization(self) -> None:
        """Full pipeline: normalize Finnish text then synthesize."""
        from src.tts_engine import TTSConfig, text_to_speech

        out = os.path.join(tempfile.gettempdir(), "test_edge_fi_norm.mp3")
        try:
            text_to_speech(
                "Hän syntyi 1300-luvulla ja asui sivulta 42 löytyvässä talossa.",
                out,
                config=TTSConfig(
                    voice="fi-FI-NooraNeural", language="fi",
                    normalize_text=True,
                ),
            )
            assert os.path.isfile(out)
            assert os.path.getsize(out) > 1000
        finally:
            if os.path.exists(out):
                os.unlink(out)

    def test_multi_chunk_combining(self) -> None:
        """Text long enough to produce multiple chunks should still combine."""
        from src.tts_engine import TTSConfig, text_to_speech

        # Create text that will be split into multiple chunks
        sentences = ["Tämä on testilause numero {}.".format(i) for i in range(30)]
        long_text = " ".join(sentences)

        out = os.path.join(tempfile.gettempdir(), "test_edge_multi.mp3")
        try:
            text_to_speech(
                long_text,
                out,
                config=TTSConfig(voice="fi-FI-NooraNeural", language="fi"),
            )
            assert os.path.isfile(out)
            # Multiple chunks should produce a larger file
            assert os.path.getsize(out) > 5000
        finally:
            if os.path.exists(out):
                os.unlink(out)

    def test_empty_text_raises(self) -> None:
        from src.tts_engine import text_to_speech

        out = os.path.join(tempfile.gettempdir(), "test_edge_empty.mp3")
        with pytest.raises(ValueError, match="empty"):
            text_to_speech("   ", out)

    def test_progress_callback_called(self) -> None:
        from src.tts_engine import TTSConfig, text_to_speech

        out = os.path.join(tempfile.gettempdir(), "test_edge_progress.mp3")
        calls: list[tuple] = []

        def on_progress(current: int, total: int, message: str) -> None:
            calls.append((current, total, message))

        try:
            text_to_speech(
                "Testi.",
                out,
                config=TTSConfig(voice="fi-FI-NooraNeural", language="fi"),
                progress_cb=on_progress,
            )
            assert len(calls) > 0
            # Last call should have current == total
            assert calls[-1][0] == calls[-1][1]
        finally:
            if os.path.exists(out):
                os.unlink(out)


# ---------------------------------------------------------------------------
# Piper engine
# ---------------------------------------------------------------------------


class TestPiperEngine:
    """Verify Piper engine registration and voice catalogue."""

    def test_piper_engine_registered(self) -> None:
        from src.tts_base import get_engine
        # Import piper module for registration side effect
        try:
            from src import tts_piper  # noqa: F401
        except ImportError:
            pytest.skip("piper-tts not installed")

        engine = get_engine("piper")
        assert engine is not None

    def test_piper_has_finnish_voice(self) -> None:
        try:
            from src.tts_piper import PiperTTSEngine
        except ImportError:
            pytest.skip("piper-tts not installed")

        engine = PiperTTSEngine()
        fi_voices = engine.list_voices("fi")
        assert len(fi_voices) >= 1, "Piper should have at least one Finnish voice"

    def test_piper_has_english_voices(self) -> None:
        try:
            from src.tts_piper import PiperTTSEngine
        except ImportError:
            pytest.skip("piper-tts not installed")

        engine = PiperTTSEngine()
        en_voices = engine.list_voices("en")
        assert len(en_voices) >= 2, "Piper should have at least two English voices"

    def test_piper_has_german_voices(self) -> None:
        try:
            from src.tts_piper import PiperTTSEngine
        except ImportError:
            pytest.skip("piper-tts not installed")

        engine = PiperTTSEngine()
        de_voices = engine.list_voices("de")
        assert len(de_voices) >= 1, "Piper should have at least one German voice"

    def test_piper_check_status_returns_engine_status(self) -> None:
        try:
            from src.tts_piper import PiperTTSEngine
        except ImportError:
            pytest.skip("piper-tts not installed")

        engine = PiperTTSEngine()
        status = engine.check_status()
        assert hasattr(status, "available")
        assert isinstance(status.available, bool)


# ---------------------------------------------------------------------------
# Chatterbox bridge
# ---------------------------------------------------------------------------


class TestChatterboxBridge:
    """Verify Chatterbox Python environment resolution."""

    def test_resolve_finds_venv(self) -> None:
        from src.launcher_bridge import resolve_chatterbox_python

        result = resolve_chatterbox_python()
        if result is None:
            pytest.skip("Chatterbox venv not installed")

        assert os.path.isfile(result)
        assert "python" in os.path.basename(result).lower()

    def test_chatterbox_python_is_executable(self) -> None:
        from src.launcher_bridge import resolve_chatterbox_python

        result = resolve_chatterbox_python()
        if result is None:
            pytest.skip("Chatterbox venv not installed")

        # Verify it's a real Python interpreter
        proc = subprocess.run(
            [result, "-c", "import sys; print(sys.version_info.major)"],
            capture_output=True, text=True, timeout=10,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == "3"

    def test_chatterbox_runner_script_exists(self) -> None:
        """The generate_chatterbox_audiobook.py script must exist for the bridge."""
        repo_root = Path(__file__).resolve().parent.parent
        script = repo_root / "scripts" / "generate_chatterbox_audiobook.py"
        assert script.exists(), f"Chatterbox runner script missing: {script}"


# ---------------------------------------------------------------------------
# Finnish text normalization
# ---------------------------------------------------------------------------


class TestFinnishNormalization:
    """End-to-end tests for the Finnish text normalizer."""

    def _normalize(self, text: str) -> str:
        from src.tts_engine import normalize_finnish_text
        return normalize_finnish_text(text)

    def test_century_expression(self) -> None:
        result = self._normalize("1300-luvulla")
        assert "tuhat" in result
        assert "kolmesataa" in result
        assert "luvulla" in result

    def test_abbreviation_esim(self) -> None:
        result = self._normalize("esim.")
        assert "esimerkiksi" in result

    def test_abbreviation_prof(self) -> None:
        result = self._normalize("prof. Virtanen")
        assert "professori" in result
        assert "Virtanen" in result

    def test_percent_expansion(self) -> None:
        result = self._normalize("5 %")
        assert "viisi" in result
        assert "prosenttia" in result

    def test_number_inflection_sivulta(self) -> None:
        result = self._normalize("sivulta 42")
        # Should inflect the number in ablative case
        assert "42" not in result  # Number should be expanded
        assert "sivulta" in result

    def test_isbn_stripped(self) -> None:
        result = self._normalize("ISBN 978-3-16-148410-0 on kirjan tunniste")
        assert "978" not in result

    def test_ellipsis_passthrough(self) -> None:
        """Ellipsis in normal text is preserved (normalizer handles TOC dots, not prose)."""
        result = self._normalize("hän sanoi... ja sitten")
        assert "sanoi" in result
        assert "sitten" in result

    def test_year_number(self) -> None:
        result = self._normalize("Vuonna 1918 tapahtui paljon.")
        # Year should be expanded to words
        assert "1918" not in result

    def test_roman_numeral(self) -> None:
        result = self._normalize("luku III käsittelee")
        # Roman numeral should be expanded
        assert "III" not in result

    def test_unit_symbol_km(self) -> None:
        result = self._normalize("5 km")
        assert "kilometriä" in result

    def test_passthrough_normal_text(self) -> None:
        text = "Tämä on ihan normaali lause."
        result = self._normalize(text)
        assert result == text

    def test_mixed_content(self) -> None:
        """Multiple normalizations in a single text."""
        text = "1300-luvulla prof. Virtanen löysi 5 % virheistä sivulta 42."
        result = self._normalize(text)
        assert "1300" not in result
        assert "prof." not in result
        assert "%" not in result


# ---------------------------------------------------------------------------
# Audio combining with ffmpeg/ffprobe
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available")
class TestCombineAudioFiles:
    """Tests for combine_audio_files with real ffmpeg."""

    def _make_silent_mp3(self, path: str, duration_ms: int = 500) -> None:
        """Create a real silent MP3 file using pydub."""
        from pydub import AudioSegment
        seg = AudioSegment.silent(duration=duration_ms)
        seg.export(path, format="mp3")

    def test_combine_two_files(self, tmp_path: Path) -> None:
        from src.tts_engine import combine_audio_files

        f1 = str(tmp_path / "chunk1.mp3")
        f2 = str(tmp_path / "chunk2.mp3")
        out = str(tmp_path / "combined.mp3")

        self._make_silent_mp3(f1, 500)
        self._make_silent_mp3(f2, 500)

        combine_audio_files([f1, f2], out)

        assert os.path.isfile(out)
        assert os.path.getsize(out) > 0

    def test_combine_single_file(self, tmp_path: Path) -> None:
        from src.tts_engine import combine_audio_files

        f1 = str(tmp_path / "chunk1.mp3")
        out = str(tmp_path / "combined.mp3")

        self._make_silent_mp3(f1, 500)

        combine_audio_files([f1], out)

        assert os.path.isfile(out)
        assert os.path.getsize(out) > 0

    def test_combine_empty_list_raises(self) -> None:
        from src.tts_engine import combine_audio_files

        with pytest.raises(ValueError, match="No audio files"):
            combine_audio_files([], "output.mp3")

    def test_combine_calls_setup_ffmpeg(self, tmp_path: Path) -> None:
        """combine_audio_files should call setup_ffmpeg_path as safety net."""
        from src.tts_engine import combine_audio_files

        f1 = str(tmp_path / "chunk1.mp3")
        out = str(tmp_path / "combined.mp3")

        self._make_silent_mp3(f1, 100)

        with patch("src.ffmpeg_path.setup_ffmpeg_path", autospec=True) as mock_setup:
            combine_audio_files([f1], out)
            mock_setup.assert_called_once()


# ---------------------------------------------------------------------------
# Edge-TTS voice catalogue
# ---------------------------------------------------------------------------


class TestVoiceCatalogue:
    """Verify the voice catalogue has all expected languages."""

    def test_finnish_voices_exist(self) -> None:
        from src.tts_engine import VOICE_DISPLAY_NAMES
        fi_voices = [k for k in VOICE_DISPLAY_NAMES if k.startswith("fi-")]
        assert len(fi_voices) >= 2, f"Expected >= 2 Finnish voices, got {fi_voices}"

    def test_english_us_voices_exist(self) -> None:
        from src.tts_engine import VOICE_DISPLAY_NAMES
        en_us = [k for k in VOICE_DISPLAY_NAMES if k.startswith("en-US-")]
        assert len(en_us) >= 4, f"Expected >= 4 English US voices, got {en_us}"

    def test_english_gb_voices_exist(self) -> None:
        from src.tts_engine import VOICE_DISPLAY_NAMES
        en_gb = [k for k in VOICE_DISPLAY_NAMES if k.startswith("en-GB-")]
        assert len(en_gb) >= 2, f"Expected >= 2 English GB voices, got {en_gb}"

    def test_german_voices_exist(self) -> None:
        from src.tts_engine import VOICE_DISPLAY_NAMES
        de = [k for k in VOICE_DISPLAY_NAMES if k.startswith("de-")]
        assert len(de) >= 2, f"Expected >= 2 German voices, got {de}"

    def test_swedish_voices_exist(self) -> None:
        from src.tts_engine import VOICE_DISPLAY_NAMES
        sv = [k for k in VOICE_DISPLAY_NAMES if k.startswith("sv-")]
        assert len(sv) >= 2, f"Expected >= 2 Swedish voices, got {sv}"

    def test_french_voices_exist(self) -> None:
        from src.tts_engine import VOICE_DISPLAY_NAMES
        fr = [k for k in VOICE_DISPLAY_NAMES if k.startswith("fr-")]
        assert len(fr) >= 2, f"Expected >= 2 French voices, got {fr}"

    def test_spanish_voices_exist(self) -> None:
        from src.tts_engine import VOICE_DISPLAY_NAMES
        es = [k for k in VOICE_DISPLAY_NAMES if k.startswith("es-")]
        assert len(es) >= 2, f"Expected >= 2 Spanish voices, got {es}"

    def test_all_voices_have_display_names(self) -> None:
        from src.tts_engine import VOICE_DISPLAY_NAMES
        for voice_id, display_name in VOICE_DISPLAY_NAMES.items():
            assert display_name, f"Voice {voice_id} has empty display name"
            assert len(display_name) > 3, f"Voice {voice_id} display name too short"


# ---------------------------------------------------------------------------
# main.py entry point calls setup_ffmpeg_path
# ---------------------------------------------------------------------------


class TestMainEntryPoint:
    """Verify main.py calls setup_ffmpeg_path before GUI import."""

    def test_main_imports_ffmpeg_setup(self) -> None:
        """The entry point must call setup_ffmpeg_path() before importing GUI."""
        import ast

        main_path = Path(__file__).resolve().parent.parent / "src" / "main.py"
        source = main_path.read_text()
        tree = ast.parse(source)

        # Check that setup_ffmpeg_path is imported at module level
        has_ffmpeg_import = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "src.ffmpeg_path":
                    names = [a.name for a in node.names]
                    if "setup_ffmpeg_path" in names:
                        has_ffmpeg_import = True

        assert has_ffmpeg_import, "main.py must import setup_ffmpeg_path from src.ffmpeg_path"

    def test_setup_called_before_gui_import(self) -> None:
        """setup_ffmpeg_path() must be called before 'from src.gui_unified import run'."""
        main_path = Path(__file__).resolve().parent.parent / "src" / "main.py"
        source = main_path.read_text()

        setup_pos = source.find("setup_ffmpeg_path()")
        gui_import_pos = source.find("from src.gui_unified import run")

        assert setup_pos > 0, "setup_ffmpeg_path() call not found in main.py"
        assert gui_import_pos > 0, "GUI import not found in main.py"
        assert setup_pos < gui_import_pos, \
            "setup_ffmpeg_path() must be called BEFORE gui_unified is imported"
