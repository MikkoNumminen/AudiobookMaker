"""Unit tests for the Qwen3-TTS VoiceDesign engine adapter.

Qwen3-TTS is a heavy GPU-only package (torch + ~4 GB of weights) that we do
NOT install in CI or the test venv. All real synthesis paths are mocked; the
tests verify that the adapter reports the right status, exposes the right
voices, guards Finnish / unsupported languages, resolves the voice-description
correctly, and wires the shared chunk/combine pipeline.

One real end-to-end smoke test is gated behind the ``gpu`` marker (and
``slow`` + ``network``) so it only runs on a machine that actually has the
GPU and the package; it is skipped everywhere else.
"""

from __future__ import annotations

import builtins
from unittest.mock import MagicMock, patch

import pytest

from src.tts_base import EngineStatus, Voice, get_engine
from src.tts_qwen_voicedesign import (
    QwenVoiceDesignEngine,
    _DEFAULT_VOICE_ID,
    _INSTALL_HINT,
    _QWEN_LANGUAGES,
    _VOICE_PRESETS,
    _resolve_instruct,
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


def _ready_engine_and_mocks(sample_rate: int = 24000):
    """Return (engine, fake_model, sys_modules_patch_dict).

    The engine's ``_load_model`` is replaced with one that installs a fake
    model whose ``generate_voice_design`` returns ``([waveform], sample_rate)``.
    The caller still has to ``patch.dict("sys.modules", ...)`` with the returned
    dict so ``check_status`` and the ``import soundfile`` succeed.
    """
    fake_qwen = MagicMock()
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    fake_sf = MagicMock()

    engine = QwenVoiceDesignEngine()
    fake_model = MagicMock()
    fake_model.generate_voice_design.return_value = ([[0.0, 0.1, 0.0]], sample_rate)

    def fake_load_model():
        engine._model = fake_model
        return fake_model

    engine._load_model = fake_load_model  # type: ignore[method-assign]

    modules = {"qwen_tts": fake_qwen, "torch": fake_torch, "soundfile": fake_sf}
    return engine, fake_model, modules


# ---------------------------------------------------------------------------
# Registration / metadata
# ---------------------------------------------------------------------------


def test_qwen_engine_is_registered() -> None:
    engine = get_engine("qwen_voicedesign")
    assert isinstance(engine, QwenVoiceDesignEngine)


class TestMetadata:
    def test_id_and_display_name(self) -> None:
        assert QwenVoiceDesignEngine.id == "qwen_voicedesign"
        assert "VoiceDesign" in QwenVoiceDesignEngine.display_name
        assert "GPU" in QwenVoiceDesignEngine.display_name

    def test_requires_gpu_flag(self) -> None:
        assert QwenVoiceDesignEngine.requires_gpu is True

    def test_supports_voice_description_flag(self) -> None:
        assert QwenVoiceDesignEngine.supports_voice_description is True

    def test_does_not_support_voice_cloning(self) -> None:
        # VoiceDesign only — the reference-audio cloning path is deliberately
        # not wired up.
        assert QwenVoiceDesignEngine.supports_voice_cloning is False

    def test_does_not_require_internet(self) -> None:
        # After the weights are cached, the engine runs fully offline.
        assert QwenVoiceDesignEngine.requires_internet is False


# ---------------------------------------------------------------------------
# check_status
# ---------------------------------------------------------------------------


class TestCheckStatus:
    def test_unavailable_when_qwen_not_installed(self) -> None:
        with patch("builtins.__import__", side_effect=_force_import_error({"qwen_tts"})):
            status = QwenVoiceDesignEngine().check_status()
        assert isinstance(status, EngineStatus)
        assert not status.available
        assert "qwen-tts" in status.reason.lower()

    def test_unavailable_when_torch_missing(self) -> None:
        fake_qwen = MagicMock()
        with patch.dict("sys.modules", {"qwen_tts": fake_qwen}), patch(
            "builtins.__import__",
            side_effect=_force_import_error({"torch"}),
        ):
            status = QwenVoiceDesignEngine().check_status()
        assert not status.available
        assert "torch" in status.reason.lower()

    def test_unavailable_when_no_cuda(self) -> None:
        fake_qwen = MagicMock()
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", {"qwen_tts": fake_qwen, "torch": fake_torch}):
            status = QwenVoiceDesignEngine().check_status()
        assert not status.available
        assert "GPU" in status.reason or "CUDA" in status.reason

    def test_available_when_everything_present(self) -> None:
        fake_qwen = MagicMock()
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        with patch.dict("sys.modules", {"qwen_tts": fake_qwen, "torch": fake_torch}):
            status = QwenVoiceDesignEngine().check_status()
        assert status.available
        assert status.reason == ""


# ---------------------------------------------------------------------------
# Languages
# ---------------------------------------------------------------------------


class TestSupportedLanguages:
    def test_returns_the_ten_qwen_languages(self) -> None:
        langs = QwenVoiceDesignEngine().supported_languages()
        assert langs == set(_QWEN_LANGUAGES)
        assert len(langs) == 10

    def test_finnish_is_excluded(self) -> None:
        # The whole point of the language guard: Finnish must never be offered.
        assert "fi" not in QwenVoiceDesignEngine().supported_languages()

    def test_english_is_supported(self) -> None:
        assert "en" in QwenVoiceDesignEngine().supported_languages()

    def test_returns_a_set(self) -> None:
        assert isinstance(QwenVoiceDesignEngine().supported_languages(), set)


# ---------------------------------------------------------------------------
# Voices
# ---------------------------------------------------------------------------


class TestVoices:
    def test_english_lists_all_presets(self) -> None:
        voices = QwenVoiceDesignEngine().list_voices("en")
        assert len(voices) == len(_VOICE_PRESETS)
        assert all(v.language == "en" for v in voices)
        assert all(isinstance(v, Voice) for v in voices)
        ids = {v.id for v in voices}
        assert ids == set(_VOICE_PRESETS)

    def test_finnish_returns_empty_list(self) -> None:
        assert QwenVoiceDesignEngine().list_voices("fi") == []

    def test_unknown_language_returns_empty_list(self) -> None:
        assert QwenVoiceDesignEngine().list_voices("xx") == []

    def test_default_voice_for_supported_language(self) -> None:
        assert QwenVoiceDesignEngine().default_voice("en") == _DEFAULT_VOICE_ID

    def test_default_voice_for_finnish_is_none(self) -> None:
        assert QwenVoiceDesignEngine().default_voice("fi") is None

    def test_default_voice_for_unknown_language_is_none(self) -> None:
        assert QwenVoiceDesignEngine().default_voice("xx") is None

    def test_default_voice_id_is_a_known_preset(self) -> None:
        assert _DEFAULT_VOICE_ID in _VOICE_PRESETS


# ---------------------------------------------------------------------------
# _resolve_instruct
# ---------------------------------------------------------------------------


class TestResolveInstruct:
    def test_free_text_description_wins(self) -> None:
        out = _resolve_instruct("A spooky whisper", _DEFAULT_VOICE_ID)
        assert out == "A spooky whisper"

    def test_strips_description_whitespace(self) -> None:
        out = _resolve_instruct("  A spooky whisper  ", _DEFAULT_VOICE_ID)
        assert out == "A spooky whisper"

    def test_none_falls_back_to_preset(self) -> None:
        out = _resolve_instruct(None, _DEFAULT_VOICE_ID)
        assert out == _VOICE_PRESETS[_DEFAULT_VOICE_ID][1]

    def test_empty_falls_back_to_preset(self) -> None:
        out = _resolve_instruct("", _DEFAULT_VOICE_ID)
        assert out == _VOICE_PRESETS[_DEFAULT_VOICE_ID][1]

    def test_whitespace_only_falls_back_to_preset(self) -> None:
        out = _resolve_instruct("   \t ", "qwen-warm-male")
        assert out == _VOICE_PRESETS["qwen-warm-male"][1]


# ---------------------------------------------------------------------------
# synthesize — validation / guards (no model needed)
# ---------------------------------------------------------------------------


class TestSynthesizeGuards:
    def test_raises_on_empty_text(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            QwenVoiceDesignEngine().synthesize("", "/tmp/out.mp3", "", "en")

    def test_raises_on_finnish(self) -> None:
        # Finnish must be hard-blocked before any GPU work — no mocks needed.
        with pytest.raises(ValueError, match="Finnish|does not support"):
            QwenVoiceDesignEngine().synthesize("moi", "/tmp/out.mp3", "", "fi")

    def test_raises_on_unsupported_language(self) -> None:
        with pytest.raises(ValueError, match="does not support language"):
            QwenVoiceDesignEngine().synthesize("hello", "/tmp/out.mp3", "", "xx")

    def test_raises_on_unknown_voice(self) -> None:
        with pytest.raises(ValueError, match="Unknown Qwen VoiceDesign voice"):
            QwenVoiceDesignEngine().synthesize(
                "hello", "/tmp/out.mp3", "not-a-voice", "en"
            )

    def test_raises_when_engine_unavailable(self) -> None:
        engine = QwenVoiceDesignEngine()
        with patch(
            "builtins.__import__", side_effect=_force_import_error({"qwen_tts"})
        ):
            with pytest.raises(RuntimeError, match="unavailable"):
                engine.synthesize("hello", "/tmp/out.mp3", "", "en")


# ---------------------------------------------------------------------------
# synthesize — happy path (mocked model)
# ---------------------------------------------------------------------------


class TestSynthesizeHappyPath:
    def test_passes_language_name_and_preset_instruct(self, tmp_path) -> None:
        engine, fake_model, modules = _ready_engine_and_mocks()
        out = tmp_path / "out.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_voicedesign.combine_audio_files"
        ) as fake_combine, patch(
            "src.tts_qwen_voicedesign.split_text_into_chunks",
            return_value=["Hello world."],
        ):
            engine.synthesize("Hello world.", str(out), _DEFAULT_VOICE_ID, "en")

        assert fake_combine.called
        kwargs = fake_model.generate_voice_design.call_args.kwargs
        # Short code -> Qwen language NAME.
        assert kwargs["language"] == "English"
        assert kwargs["text"] == "Hello world."
        # No description given -> the preset's canned instruct is used.
        assert kwargs["instruct"] == _VOICE_PRESETS[_DEFAULT_VOICE_ID][1]

    def test_free_text_description_overrides_preset(self, tmp_path) -> None:
        engine, fake_model, modules = _ready_engine_and_mocks()
        out = tmp_path / "out.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_voicedesign.combine_audio_files"
        ), patch(
            "src.tts_qwen_voicedesign.split_text_into_chunks",
            return_value=["Hello world."],
        ):
            engine.synthesize(
                "Hello world.",
                str(out),
                _DEFAULT_VOICE_ID,
                "en",
                voice_description="A deep pirate growl",
            )
        kwargs = fake_model.generate_voice_design.call_args.kwargs
        assert kwargs["instruct"] == "A deep pirate growl"

    def test_reference_audio_is_ignored_not_clone(self, tmp_path) -> None:
        # Passing reference_audio must NOT raise and must NOT be forwarded to
        # the model — VoiceDesign has no cloning path.
        engine, fake_model, modules = _ready_engine_and_mocks()
        out = tmp_path / "out.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_voicedesign.combine_audio_files"
        ), patch(
            "src.tts_qwen_voicedesign.split_text_into_chunks",
            return_value=["Hello world."],
        ):
            engine.synthesize(
                "Hello world.",
                str(out),
                _DEFAULT_VOICE_ID,
                "en",
                reference_audio="/nonexistent/ref.wav",
            )
        kwargs = fake_model.generate_voice_design.call_args.kwargs
        assert "reference_audio" not in kwargs
        assert "reference_wav_path" not in kwargs

    def test_uses_model_reported_sample_rate(self, tmp_path) -> None:
        # sf.write must receive the sample rate that generate_voice_design
        # returned, not a hard-coded value.
        engine, fake_model, modules = _ready_engine_and_mocks(sample_rate=16000)

        captured_rates: list[int] = []

        def fake_write(path, wav, rate):
            captured_rates.append(rate)

        modules["soundfile"].write = fake_write

        out = tmp_path / "out.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_voicedesign.combine_audio_files"
        ), patch(
            "src.tts_qwen_voicedesign.split_text_into_chunks",
            return_value=["chunk one", "chunk two"],
        ):
            engine.synthesize("whatever", str(out), _DEFAULT_VOICE_ID, "en")

        assert captured_rates, "expected at least one chunk written"
        assert all(r == 16000 for r in captured_rates), captured_rates

    def test_raises_when_no_chunks(self, tmp_path) -> None:
        engine, _fake_model, modules = _ready_engine_and_mocks()
        out = tmp_path / "out.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_voicedesign.split_text_into_chunks", return_value=[]
        ):
            with pytest.raises(ValueError, match="no chunks"):
                engine.synthesize("   .", str(out), _DEFAULT_VOICE_ID, "en")


# ---------------------------------------------------------------------------
# Text normalization — only for languages the normalizer handles
# ---------------------------------------------------------------------------


class TestNormalization:
    """English is normalized before chunking (the cross-engine convention).
    The other Qwen languages have no normalizer and ``normalize_text`` would
    raise on them, so they must pass through untouched."""

    def test_english_is_normalized_before_chunking(self, tmp_path) -> None:
        engine, _fake_model, modules = _ready_engine_and_mocks()
        out = tmp_path / "out.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_voicedesign.normalize_text", return_value="NORMALIZED"
        ) as spy, patch(
            "src.tts_qwen_voicedesign.split_text_into_chunks", return_value=[]
        ) as chunker:
            with pytest.raises(ValueError):  # empty chunks raise right after
                engine.synthesize("Mr. Smith paid $5.", str(out), _DEFAULT_VOICE_ID, "en")

        spy.assert_called_once_with("Mr. Smith paid $5.", "en")
        assert chunker.call_args.args[0] == "NORMALIZED"

    def test_german_is_not_normalized(self, tmp_path) -> None:
        # German is a valid Qwen language but the normalizer doesn't handle it;
        # normalize_text must not be called (it would raise).
        engine, _fake_model, modules = _ready_engine_and_mocks()
        out = tmp_path / "out.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_voicedesign.normalize_text"
        ) as spy, patch(
            "src.tts_qwen_voicedesign.split_text_into_chunks", return_value=[]
        ) as chunker:
            with pytest.raises(ValueError):
                engine.synthesize("Guten Tag.", str(out), _DEFAULT_VOICE_ID, "de")

        spy.assert_not_called()
        assert chunker.call_args.args[0] == "Guten Tag."


# ---------------------------------------------------------------------------
# Real GPU smoke test (skipped unless CUDA + qwen-tts are actually present)
# ---------------------------------------------------------------------------


def _gpu_and_qwen_available() -> bool:
    try:
        import torch  # noqa: WPS433
        import qwen_tts  # noqa: F401, WPS433

        return bool(torch.cuda.is_available())
    except Exception:
        return False


@pytest.mark.gpu
@pytest.mark.slow
@pytest.mark.network  # first run downloads the weights from Hugging Face
@pytest.mark.skipif(
    not _gpu_and_qwen_available(),
    reason="needs a CUDA GPU and `pip install qwen-tts`",
)
def test_real_synthesis_smoke(tmp_path) -> None:
    out = tmp_path / "qwen_smoke.mp3"
    QwenVoiceDesignEngine().synthesize(
        "Hello there, and welcome to this short test.",
        str(out),
        "",
        "en",
        voice_description="A calm male narrator in his mid-30s.",
    )
    assert out.exists()
    assert out.stat().st_size > 0
