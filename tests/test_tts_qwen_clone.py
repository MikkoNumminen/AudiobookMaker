"""Unit tests for the Qwen3-TTS voice-clone engine adapter.

The heavy GPU package and faster-whisper are never installed in CI, so all real
synthesis and transcription are mocked. The clone-specific behavior pinned here:
the reference clip is required and path-validated, and ``ref_text`` is resolved
in priority order (explicit env override -> Whisper auto-transcribe -> x-vector
fallback). A GPU smoke test runs only on a real box with a reference clip.
"""

from __future__ import annotations

import builtins
from unittest.mock import MagicMock, patch

# numpy at module scope: synthesize lazily imports it and the patch.dict
# sys.modules restores below would otherwise drop it (see the VoiceDesign tests).
import numpy as np  # noqa: F401
import pytest

from src.tts_base import EngineStatus, get_engine
from src.tts_qwen_common import QWEN_LANGUAGES
from src.tts_qwen_clone import (
    QwenVoiceCloneEngine,
    _HF_MODEL_ID,
    _transcribe_reference,
)


def _force_import_error(missing: set[str]):
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name in missing or name.split(".")[0] in missing:
            raise ImportError(f"fake: {name} missing")
        return real_import(name, *args, **kwargs)

    return fake_import


def _ready_engine_and_mocks(sample_rate: int = 24000):
    fake_qwen = MagicMock()
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    fake_sf = MagicMock()

    engine = QwenVoiceCloneEngine()
    fake_model = MagicMock()
    fake_model.generate_voice_clone.return_value = ([[0.0, 0.1, 0.0]], sample_rate)

    def fake_load_model():
        engine._model = fake_model
        return fake_model

    engine._load_model = fake_load_model  # type: ignore[method-assign]
    modules = {"qwen_tts": fake_qwen, "torch": fake_torch, "soundfile": fake_sf}
    return engine, fake_model, modules


def _ref_wav(tmp_path):
    p = tmp_path / "ref.wav"
    p.write_bytes(b"\x00\x00")  # contents never read (model is mocked)
    return str(p)


# ---------------------------------------------------------------------------
# Registration / metadata
# ---------------------------------------------------------------------------


def test_clone_engine_is_registered() -> None:
    assert isinstance(get_engine("qwen_clone"), QwenVoiceCloneEngine)


class TestMetadata:
    def test_id_and_display(self) -> None:
        assert QwenVoiceCloneEngine.id == "qwen_clone"
        assert "Clone" in QwenVoiceCloneEngine.display_name
        assert "GPU" in QwenVoiceCloneEngine.display_name

    def test_supports_cloning(self) -> None:
        assert QwenVoiceCloneEngine.supports_voice_cloning is True

    def test_no_voice_description(self) -> None:
        # The Base clone path has no instruct; the voice comes from the clip.
        assert QwenVoiceCloneEngine.supports_voice_description is False

    def test_uses_base_checkpoint(self) -> None:
        assert QwenVoiceCloneEngine.MODEL_ID == _HF_MODEL_ID
        assert _HF_MODEL_ID.endswith("Base")


# ---------------------------------------------------------------------------
# Languages / voices (no fixed catalogue — the clip is the voice)
# ---------------------------------------------------------------------------


class TestLanguagesAndVoices:
    def test_ten_languages_no_finnish(self) -> None:
        langs = QwenVoiceCloneEngine().supported_languages()
        assert langs == set(QWEN_LANGUAGES)
        assert "fi" not in langs

    def test_no_preset_voices(self) -> None:
        assert QwenVoiceCloneEngine().list_voices("en") == []

    def test_no_default_voice(self) -> None:
        assert QwenVoiceCloneEngine().default_voice("en") is None


# ---------------------------------------------------------------------------
# synthesize — guards
# ---------------------------------------------------------------------------


class TestGuards:
    def test_empty_text(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            QwenVoiceCloneEngine().synthesize("", "/tmp/o.mp3", "", "en")

    def test_finnish_blocked(self) -> None:
        with pytest.raises(ValueError, match="Finnish"):
            QwenVoiceCloneEngine().synthesize("moi", "/tmp/o.mp3", "", "fi")

    def test_missing_reference_raises(self) -> None:
        # The clip is required; no reference_audio -> clear error before any load.
        with pytest.raises(ValueError, match="requires a reference"):
            QwenVoiceCloneEngine().synthesize("hi", "/tmp/o.mp3", "", "en")

    def test_nonexistent_reference_raises(self) -> None:
        with pytest.raises(ValueError, match="not found"):
            QwenVoiceCloneEngine().synthesize(
                "hi", "/tmp/o.mp3", "", "en", reference_audio="/nope/ref.wav"
            )

    def test_unavailable(self, tmp_path) -> None:
        ref = _ref_wav(tmp_path)
        engine = QwenVoiceCloneEngine()
        with patch("builtins.__import__", side_effect=_force_import_error({"qwen_tts"})):
            with pytest.raises(RuntimeError, match="unavailable"):
                engine.synthesize("hi", "/tmp/o.mp3", "", "en", reference_audio=ref)


# ---------------------------------------------------------------------------
# ref_text resolution: env override -> Whisper -> x-vector fallback
# ---------------------------------------------------------------------------


class TestRefTextResolution:
    def test_env_override_supplies_ref_text(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("AUDIOBOOKMAKER_QWEN_REF_TEXT", "the reference words")
        ref = _ref_wav(tmp_path)
        engine, fake_model, modules = _ready_engine_and_mocks()
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_common.combine_audio_files"
        ), patch(
            "src.tts_qwen_common.split_text_into_chunks", return_value=["hi"]
        ):
            engine.synthesize("hi", str(tmp_path / "o.mp3"), "", "en", reference_audio=ref)
        kwargs = fake_model.generate_voice_clone.call_args.kwargs
        assert kwargs["ref_text"] == "the reference words"
        assert kwargs["ref_audio"] == ref
        assert "x_vector_only_mode" not in kwargs

    def test_whisper_transcript_used_when_no_override(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("AUDIOBOOKMAKER_QWEN_REF_TEXT", raising=False)
        ref = _ref_wav(tmp_path)
        engine, fake_model, modules = _ready_engine_and_mocks()
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_clone._transcribe_reference", return_value="whisper text"
        ), patch("src.tts_qwen_common.combine_audio_files"), patch(
            "src.tts_qwen_common.split_text_into_chunks", return_value=["hi"]
        ):
            engine.synthesize("hi", str(tmp_path / "o.mp3"), "", "en", reference_audio=ref)
        kwargs = fake_model.generate_voice_clone.call_args.kwargs
        assert kwargs["ref_text"] == "whisper text"
        assert "x_vector_only_mode" not in kwargs

    def test_x_vector_fallback_when_no_transcript(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("AUDIOBOOKMAKER_QWEN_REF_TEXT", raising=False)
        ref = _ref_wav(tmp_path)
        engine, fake_model, modules = _ready_engine_and_mocks()
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_clone._transcribe_reference", return_value=None
        ), patch("src.tts_qwen_common.combine_audio_files"), patch(
            "src.tts_qwen_common.split_text_into_chunks", return_value=["hi"]
        ):
            engine.synthesize("hi", str(tmp_path / "o.mp3"), "", "en", reference_audio=ref)
        kwargs = fake_model.generate_voice_clone.call_args.kwargs
        assert kwargs["x_vector_only_mode"] is True
        assert "ref_text" not in kwargs

    def test_blank_env_override_falls_through_to_transcribe(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("AUDIOBOOKMAKER_QWEN_REF_TEXT", "   ")  # whitespace only
        ref = _ref_wav(tmp_path)
        engine, fake_model, modules = _ready_engine_and_mocks()
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_clone._transcribe_reference", return_value="from whisper"
        ), patch("src.tts_qwen_common.combine_audio_files"), patch(
            "src.tts_qwen_common.split_text_into_chunks", return_value=["hi"]
        ):
            engine.synthesize("hi", str(tmp_path / "o.mp3"), "", "en", reference_audio=ref)
        assert fake_model.generate_voice_clone.call_args.kwargs["ref_text"] == "from whisper"


# ---------------------------------------------------------------------------
# _transcribe_reference helper
# ---------------------------------------------------------------------------


class TestTranscribeReference:
    def test_returns_none_without_faster_whisper(self) -> None:
        with patch("builtins.__import__", side_effect=_force_import_error({"faster_whisper"})):
            assert _transcribe_reference("/whatever.wav") is None

    def test_joins_segments(self) -> None:
        seg1, seg2 = MagicMock(), MagicMock()
        seg1.text, seg2.text = "Hello", "world."
        fake_model = MagicMock()
        fake_model.transcribe.return_value = ([seg1, seg2], MagicMock())
        fake_fw = MagicMock()
        fake_fw.WhisperModel.return_value = fake_model
        with patch.dict("sys.modules", {"faster_whisper": fake_fw}):
            out = _transcribe_reference("/ref.wav")
        assert out == "Hello world."

    def test_returns_none_on_transcription_error(self) -> None:
        fake_fw = MagicMock()
        fake_fw.WhisperModel.side_effect = RuntimeError("cuda oom")
        with patch.dict("sys.modules", {"faster_whisper": fake_fw}):
            assert _transcribe_reference("/ref.wav") is None


# ---------------------------------------------------------------------------
# Language mapping + load id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code,name", sorted(QWEN_LANGUAGES.items()))
def test_language_code_maps_to_qwen_name(tmp_path, code, name, monkeypatch) -> None:
    monkeypatch.setenv("AUDIOBOOKMAKER_QWEN_REF_TEXT", "ref words")  # skip whisper
    ref = _ref_wav(tmp_path)
    engine, fake_model, modules = _ready_engine_and_mocks()
    with patch.dict("sys.modules", modules), patch(
        "src.tts_qwen_common.combine_audio_files"
    ), patch("src.tts_qwen_common.split_text_into_chunks", return_value=["hello"]):
        engine.synthesize("hello", str(tmp_path / "o.mp3"), "", code, reference_audio=ref)
    assert fake_model.generate_voice_clone.call_args.kwargs["language"] == name


def test_load_model_uses_base_id(monkeypatch) -> None:
    monkeypatch.delenv("AUDIOBOOKMAKER_QWEN_ATTN", raising=False)
    monkeypatch.delenv("AUDIOBOOKMAKER_QWEN_DEVICE", raising=False)
    fake_qwen = MagicMock()
    fake_torch = MagicMock()
    engine = QwenVoiceCloneEngine()
    with patch.dict("sys.modules", {"qwen_tts": fake_qwen, "torch": fake_torch}):
        engine._load_model()
    call = fake_qwen.Qwen3TTSModel.from_pretrained.call_args
    assert call.args[0] == _HF_MODEL_ID
    assert {"device_map", "dtype", "attn_implementation"} <= set(call.kwargs)


def test_check_status_available(tmp_path) -> None:
    fake_qwen = MagicMock()
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    with patch.dict("sys.modules", {"qwen_tts": fake_qwen, "torch": fake_torch}):
        status = QwenVoiceCloneEngine().check_status()
    assert isinstance(status, EngineStatus) and status.available


# ---------------------------------------------------------------------------
# Real GPU smoke test (needs CUDA + qwen-tts + a reference clip via env)
# ---------------------------------------------------------------------------


def _gpu_and_qwen_available() -> bool:
    try:
        import torch
        import qwen_tts  # noqa: F401

        return bool(torch.cuda.is_available())
    except Exception:
        return False


@pytest.mark.gpu
@pytest.mark.slow
@pytest.mark.network  # first run downloads the Base weights
def test_real_clone_smoke(tmp_path) -> None:
    import os

    if not _gpu_and_qwen_available():
        pytest.skip("needs a CUDA GPU and `pip install qwen-tts`")
    ref = os.environ.get("AUDIOBOOKMAKER_QWEN_CLONE_REF")
    if not ref or not os.path.isfile(ref):
        pytest.skip("set AUDIOBOOKMAKER_QWEN_CLONE_REF to a reference WAV to run")
    out = tmp_path / "qwen_clone_smoke.mp3"
    QwenVoiceCloneEngine().synthesize(
        "Hello there, and welcome to this short test.",
        str(out),
        "",
        "en",
        reference_audio=ref,
    )
    assert out.exists() and out.stat().st_size > 0
