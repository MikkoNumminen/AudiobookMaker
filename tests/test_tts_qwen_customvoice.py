"""Unit tests for the Qwen3-TTS CustomVoice engine adapter.

Like the VoiceDesign tests, the heavy GPU package is never installed in CI, so
all real synthesis is mocked; one GPU-gated smoke test runs only on a real box.
CustomVoice differs from VoiceDesign in three ways exercised here: the voice ids
are the 9 preset speakers, the per-chunk call is ``generate_custom_voice``, and
``voice_description`` becomes an *optional* style ``instruct`` (omitted when not
given) rather than the required voice description.
"""

from __future__ import annotations

import builtins
from unittest.mock import MagicMock, patch

# numpy at module scope: synthesize lazily imports it and the patch.dict
# sys.modules restores below would otherwise drop it (see the VoiceDesign tests).
import numpy as np  # noqa: F401
import pytest

from src.tts_base import EngineStatus, Voice, get_engine
from src.tts_qwen_common import INSTALL_HINT, QWEN_LANGUAGES
from src.tts_qwen_customvoice import (
    QwenCustomVoiceEngine,
    _DEFAULT_SPEAKER,
    _HF_MODEL_ID,
    _SPEAKERS,
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

    engine = QwenCustomVoiceEngine()
    fake_model = MagicMock()
    fake_model.generate_custom_voice.return_value = ([[0.0, 0.1, 0.0]], sample_rate)

    def fake_load_model():
        engine._model = fake_model
        return fake_model

    engine._load_model = fake_load_model  # type: ignore[method-assign]
    modules = {"qwen_tts": fake_qwen, "torch": fake_torch, "soundfile": fake_sf}
    return engine, fake_model, modules


# ---------------------------------------------------------------------------
# Registration / metadata
# ---------------------------------------------------------------------------


def test_customvoice_engine_is_registered() -> None:
    assert isinstance(get_engine("qwen_customvoice"), QwenCustomVoiceEngine)


class TestMetadata:
    def test_id_and_display_name(self) -> None:
        assert QwenCustomVoiceEngine.id == "qwen_customvoice"
        assert "CustomVoice" in QwenCustomVoiceEngine.display_name
        assert "GPU" in QwenCustomVoiceEngine.display_name

    def test_requires_gpu(self) -> None:
        assert QwenCustomVoiceEngine.requires_gpu is True

    def test_supports_voice_description(self) -> None:
        assert QwenCustomVoiceEngine.supports_voice_description is True

    def test_does_not_support_cloning(self) -> None:
        assert QwenCustomVoiceEngine.supports_voice_cloning is False

    def test_no_internet(self) -> None:
        assert QwenCustomVoiceEngine.requires_internet is False

    def test_uses_customvoice_checkpoint(self) -> None:
        assert QwenCustomVoiceEngine.MODEL_ID == _HF_MODEL_ID
        assert _HF_MODEL_ID.endswith("CustomVoice")


# ---------------------------------------------------------------------------
# check_status
# ---------------------------------------------------------------------------


class TestCheckStatus:
    def test_unavailable_without_qwen(self) -> None:
        with patch("builtins.__import__", side_effect=_force_import_error({"qwen_tts"})):
            status = QwenCustomVoiceEngine().check_status()
        assert isinstance(status, EngineStatus)
        assert not status.available
        assert status.reason == INSTALL_HINT

    def test_unavailable_without_cuda(self) -> None:
        fake_qwen = MagicMock()
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", {"qwen_tts": fake_qwen, "torch": fake_torch}):
            status = QwenCustomVoiceEngine().check_status()
        assert not status.available
        assert "GPU" in status.reason or "CUDA" in status.reason

    def test_available(self) -> None:
        fake_qwen = MagicMock()
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        with patch.dict("sys.modules", {"qwen_tts": fake_qwen, "torch": fake_torch}):
            status = QwenCustomVoiceEngine().check_status()
        assert status.available


# ---------------------------------------------------------------------------
# Languages / voices
# ---------------------------------------------------------------------------


class TestLanguagesAndVoices:
    def test_ten_languages_no_finnish(self) -> None:
        langs = QwenCustomVoiceEngine().supported_languages()
        assert langs == set(QWEN_LANGUAGES)
        assert "fi" not in langs

    def test_lists_nine_speakers(self) -> None:
        voices = QwenCustomVoiceEngine().list_voices("en")
        assert len(voices) == 9
        assert {v.id for v in voices} == set(_SPEAKERS)
        assert all(isinstance(v, Voice) for v in voices)
        assert all(v.language == "en" for v in voices)

    def test_speaker_gender_surfaced(self) -> None:
        voices = {v.id: v for v in QwenCustomVoiceEngine().list_voices("en")}
        assert voices["Vivian"].gender == "female"
        assert voices["Ryan"].gender == "male"

    def test_display_name_formats_underscored_speakers(self) -> None:
        # Underscore -> space in the label, gender appended. Expected strings
        # are written out literally so this pins the formatting, not echoes it.
        voices = {v.id: v for v in QwenCustomVoiceEngine().list_voices("en")}
        assert voices["Uncle_Fu"].display_name == "Uncle Fu (male)"
        assert voices["Ono_Anna"].display_name == "Ono Anna (female)"
        assert voices["Vivian"].display_name == "Vivian (female)"

    def test_finnish_lists_no_voices(self) -> None:
        assert QwenCustomVoiceEngine().list_voices("fi") == []

    def test_unknown_language_lists_no_voices(self) -> None:
        assert QwenCustomVoiceEngine().list_voices("xx") == []

    def test_default_speaker(self) -> None:
        assert QwenCustomVoiceEngine().default_voice("en") == _DEFAULT_SPEAKER
        assert _DEFAULT_SPEAKER in _SPEAKERS

    def test_default_voice_none_for_finnish(self) -> None:
        assert QwenCustomVoiceEngine().default_voice("fi") is None


# ---------------------------------------------------------------------------
# synthesize — guards
# ---------------------------------------------------------------------------


class TestGuards:
    def test_empty_text(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            QwenCustomVoiceEngine().synthesize("", "/tmp/o.mp3", "", "en")

    def test_finnish_blocked(self) -> None:
        with pytest.raises(ValueError, match="Finnish"):
            QwenCustomVoiceEngine().synthesize("moi", "/tmp/o.mp3", "", "fi")

    def test_unsupported_language(self) -> None:
        with pytest.raises(ValueError, match="does not support language"):
            QwenCustomVoiceEngine().synthesize("hi", "/tmp/o.mp3", "", "xx")

    def test_unknown_speaker(self) -> None:
        with pytest.raises(ValueError, match="Unknown Qwen CustomVoice speaker"):
            QwenCustomVoiceEngine().synthesize("hi", "/tmp/o.mp3", "Bogus", "en")

    def test_unavailable(self) -> None:
        engine = QwenCustomVoiceEngine()
        with patch("builtins.__import__", side_effect=_force_import_error({"qwen_tts"})):
            with pytest.raises(RuntimeError, match="unavailable"):
                engine.synthesize("hi", "/tmp/o.mp3", "", "en")


# ---------------------------------------------------------------------------
# synthesize — happy path (mocked)
# ---------------------------------------------------------------------------


class TestSynthesize:
    def test_passes_speaker_and_language_name_without_instruct(self, tmp_path) -> None:
        engine, fake_model, modules = _ready_engine_and_mocks()
        out = tmp_path / "o.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_common.combine_audio_files"
        ), patch(
            "src.tts_qwen_common.split_text_into_chunks", return_value=["Hello world."]
        ):
            engine.synthesize("Hello world.", str(out), "Ryan", "en")
        kwargs = fake_model.generate_custom_voice.call_args.kwargs
        assert kwargs["language"] == "English"
        assert kwargs["speaker"] == "Ryan"
        assert kwargs["text"] == "Hello world."
        # No description -> instruct is omitted entirely (it's optional).
        assert "instruct" not in kwargs

    def test_voice_description_becomes_style_instruct(self, tmp_path) -> None:
        engine, fake_model, modules = _ready_engine_and_mocks()
        out = tmp_path / "o.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_common.combine_audio_files"
        ), patch(
            "src.tts_qwen_common.split_text_into_chunks", return_value=["Hello world."]
        ):
            engine.synthesize(
                "Hello world.", str(out), "Vivian", "en",
                voice_description="read this in an excited tone",
            )
        kwargs = fake_model.generate_custom_voice.call_args.kwargs
        assert kwargs["instruct"] == "read this in an excited tone"
        assert kwargs["speaker"] == "Vivian"

    def test_whitespace_only_description_omits_instruct(self, tmp_path) -> None:
        engine, fake_model, modules = _ready_engine_and_mocks()
        out = tmp_path / "o.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_common.combine_audio_files"
        ), patch(
            "src.tts_qwen_common.split_text_into_chunks", return_value=["hi"]
        ):
            engine.synthesize("hi", str(out), "Vivian", "en", voice_description="   ")
        # A whitespace-only style description is treated as "no instruct".
        assert "instruct" not in fake_model.generate_custom_voice.call_args.kwargs

    def test_empty_voice_id_uses_default_speaker(self, tmp_path) -> None:
        engine, fake_model, modules = _ready_engine_and_mocks()
        out = tmp_path / "o.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_common.combine_audio_files"
        ), patch(
            "src.tts_qwen_common.split_text_into_chunks", return_value=["hi"]
        ):
            engine.synthesize("hi", str(out), "", "en")
        assert fake_model.generate_custom_voice.call_args.kwargs["speaker"] == _DEFAULT_SPEAKER

    def test_reference_audio_is_ignored(self, tmp_path) -> None:
        engine, fake_model, modules = _ready_engine_and_mocks()
        out = tmp_path / "o.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_common.combine_audio_files"
        ), patch(
            "src.tts_qwen_common.split_text_into_chunks", return_value=["hi"]
        ):
            engine.synthesize(
                "hi", str(out), "Vivian", "en", reference_audio="/nope/ref.wav"
            )
        kwargs = fake_model.generate_custom_voice.call_args.kwargs
        assert "reference_audio" not in kwargs
        assert "reference_wav_path" not in kwargs

    def test_uses_model_reported_sample_rate(self, tmp_path) -> None:
        engine, fake_model, modules = _ready_engine_and_mocks(sample_rate=16000)
        captured: list = []
        modules["soundfile"].write = lambda p, w, r: captured.append(r)
        out = tmp_path / "o.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_common.combine_audio_files"
        ), patch(
            "src.tts_qwen_common.split_text_into_chunks", return_value=["a", "b"]
        ):
            engine.synthesize("x", str(out), "Vivian", "en")
        assert captured and all(r == 16000 for r in captured)

    def test_empty_model_output_raises(self, tmp_path) -> None:
        engine, fake_model, modules = _ready_engine_and_mocks()
        fake_model.generate_custom_voice.return_value = ([], 24000)
        out = tmp_path / "o.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_common.combine_audio_files"
        ), patch(
            "src.tts_qwen_common.split_text_into_chunks", return_value=["hi"]
        ):
            with pytest.raises(RuntimeError, match="no audio"):
                engine.synthesize("hi", str(out), "Vivian", "en")


@pytest.mark.parametrize("code,name", sorted(QWEN_LANGUAGES.items()))
def test_language_code_maps_to_qwen_name(tmp_path, code, name) -> None:
    engine, fake_model, modules = _ready_engine_and_mocks()
    out = tmp_path / "o.mp3"
    with patch.dict("sys.modules", modules), patch(
        "src.tts_qwen_common.combine_audio_files"
    ), patch(
        "src.tts_qwen_common.split_text_into_chunks", return_value=["hello"]
    ):
        engine.synthesize("hello", str(out), "Vivian", code)
    assert fake_model.generate_custom_voice.call_args.kwargs["language"] == name


# ---------------------------------------------------------------------------
# from_pretrained loads the CustomVoice checkpoint
# ---------------------------------------------------------------------------


def test_load_model_uses_customvoice_id(monkeypatch) -> None:
    monkeypatch.delenv("AUDIOBOOKMAKER_QWEN_ATTN", raising=False)
    monkeypatch.delenv("AUDIOBOOKMAKER_QWEN_DEVICE", raising=False)
    fake_qwen = MagicMock()
    fake_torch = MagicMock()
    engine = QwenCustomVoiceEngine()
    with patch.dict("sys.modules", {"qwen_tts": fake_qwen, "torch": fake_torch}):
        engine._load_model()
    call = fake_qwen.Qwen3TTSModel.from_pretrained.call_args
    assert call.args[0] == _HF_MODEL_ID
    assert {"device_map", "dtype", "attn_implementation"} <= set(call.kwargs)


# ---------------------------------------------------------------------------
# Real GPU smoke test (skipped unless CUDA + qwen-tts present)
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
@pytest.mark.network  # first run downloads the CustomVoice weights
def test_real_synthesis_smoke(tmp_path) -> None:
    if not _gpu_and_qwen_available():
        pytest.skip("needs a CUDA GPU and `pip install qwen-tts`")
    out = tmp_path / "qwen_cv_smoke.mp3"
    QwenCustomVoiceEngine().synthesize(
        "Hello there, and welcome to this short test.",
        str(out),
        "Vivian",
        "en",
    )
    assert out.exists() and out.stat().st_size > 0
