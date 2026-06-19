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

# Imported at module scope on purpose: synthesize() lazily `import numpy`, and
# the patch.dict("sys.modules", ...) helpers below restore sys.modules on exit.
# If numpy were first imported inside a patched context it would be dropped on
# restore, and re-importing its C extension fails with "cannot load module more
# than once per process". Keeping it in the baseline snapshot avoids that.
import numpy as np
import pytest

from src.tts_base import EngineStatus, Voice, get_engine
from src.tts_qwen_common import INSTALL_HINT, to_cpu_float32_mono
from src.tts_qwen_voicedesign import (
    QwenVoiceDesignEngine,
    _DEFAULT_VOICE_ID,
    _HF_MODEL_ID,
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
        # Pin the exact hint text (makes the imported constant load-bearing).
        assert status.reason == INSTALL_HINT
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
        # Match "Finnish" specifically so the test fails if the Finnish-naming
        # is ever dropped from the error (the constraint worth pinning).
        with pytest.raises(ValueError, match="Finnish"):
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
            "src.tts_qwen_common.combine_audio_files"
        ) as fake_combine, patch(
            "src.tts_qwen_common.split_text_into_chunks",
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
            "src.tts_qwen_common.combine_audio_files"
        ), patch(
            "src.tts_qwen_common.split_text_into_chunks",
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
            "src.tts_qwen_common.combine_audio_files"
        ), patch(
            "src.tts_qwen_common.split_text_into_chunks",
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
            "src.tts_qwen_common.combine_audio_files"
        ), patch(
            "src.tts_qwen_common.split_text_into_chunks",
            return_value=["chunk one", "chunk two"],
        ):
            engine.synthesize("whatever", str(out), _DEFAULT_VOICE_ID, "en")

        assert captured_rates, "expected at least one chunk written"
        assert all(r == 16000 for r in captured_rates), captured_rates

    def test_raises_when_no_chunks(self, tmp_path) -> None:
        engine, _fake_model, modules = _ready_engine_and_mocks()
        out = tmp_path / "out.mp3"
        with patch.dict("sys.modules", modules), patch(
            "src.tts_qwen_common.split_text_into_chunks", return_value=[]
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
            "src.tts_qwen_common.normalize_text", return_value="NORMALIZED"
        ) as spy, patch(
            "src.tts_qwen_common.split_text_into_chunks", return_value=[]
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
            "src.tts_qwen_common.normalize_text"
        ) as spy, patch(
            "src.tts_qwen_common.split_text_into_chunks", return_value=[]
        ) as chunker:
            with pytest.raises(ValueError):
                engine.synthesize("Guten Tag.", str(out), _DEFAULT_VOICE_ID, "de")

        spy.assert_not_called()
        assert chunker.call_args.args[0] == "Guten Tag."


# ---------------------------------------------------------------------------
# _load_model — from_pretrained signature + attn validation
# ---------------------------------------------------------------------------


class TestLoadModelSignature:
    """The real from_pretrained call is otherwise only exercised by the
    GPU-gated smoke test, so a wrong kwarg name (e.g. torch_dtype vs dtype) or
    model id would pass CI silently. Pin it with a mocked qwen_tts."""

    def _load_with_mocks(self, monkeypatch):
        monkeypatch.delenv("AUDIOBOOKMAKER_QWEN_ATTN", raising=False)
        monkeypatch.delenv("AUDIOBOOKMAKER_QWEN_DEVICE", raising=False)
        fake_qwen = MagicMock()
        fake_torch = MagicMock()
        engine = QwenVoiceDesignEngine()
        with patch.dict("sys.modules", {"qwen_tts": fake_qwen, "torch": fake_torch}):
            engine._load_model()
        return fake_qwen.Qwen3TTSModel.from_pretrained.call_args

    def test_from_pretrained_model_id_and_kwargs(self, monkeypatch) -> None:
        call = self._load_with_mocks(monkeypatch)
        # model id is the first positional arg.
        assert call.args[0] == _HF_MODEL_ID
        # exact kwarg names matter (dtype, NOT the historical torch_dtype).
        assert {"device_map", "dtype", "attn_implementation"} <= set(call.kwargs)
        assert call.kwargs["device_map"] == "cuda:0"
        assert call.kwargs["attn_implementation"] == "sdpa"

    def test_attn_env_var_is_forwarded(self, monkeypatch) -> None:
        monkeypatch.delenv("AUDIOBOOKMAKER_QWEN_DEVICE", raising=False)
        monkeypatch.setenv("AUDIOBOOKMAKER_QWEN_ATTN", "flash_attention_2")
        fake_qwen = MagicMock()
        fake_torch = MagicMock()
        engine = QwenVoiceDesignEngine()
        with patch.dict("sys.modules", {"qwen_tts": fake_qwen, "torch": fake_torch}):
            engine._load_model()
        call = fake_qwen.Qwen3TTSModel.from_pretrained.call_args
        assert call.kwargs["attn_implementation"] == "flash_attention_2"

    def test_invalid_attn_env_var_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("AUDIOBOOKMAKER_QWEN_ATTN", "sdap")  # typo
        fake_qwen = MagicMock()
        fake_torch = MagicMock()
        engine = QwenVoiceDesignEngine()
        with patch.dict("sys.modules", {"qwen_tts": fake_qwen, "torch": fake_torch}):
            with pytest.raises(ValueError, match="AUDIOBOOKMAKER_QWEN_ATTN"):
                engine._load_model()


# ---------------------------------------------------------------------------
# Language short-code -> Qwen NAME mapping (at the model-call boundary)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code,name", sorted(_QWEN_LANGUAGES.items()))
def test_language_code_maps_to_qwen_name(tmp_path, code, name) -> None:
    """A swapped mapping (e.g. de -> French) would silently synthesize the
    wrong language; assert each code reaches the model as its correct NAME."""
    engine, fake_model, modules = _ready_engine_and_mocks()
    out = tmp_path / "out.mp3"
    with patch.dict("sys.modules", modules), patch(
        "src.tts_qwen_common.combine_audio_files"
    ), patch(
        "src.tts_qwen_common.split_text_into_chunks", return_value=["hello"]
    ):
        engine.synthesize("hello", str(out), _DEFAULT_VOICE_ID, code)
    assert fake_model.generate_voice_design.call_args.kwargs["language"] == name


# ---------------------------------------------------------------------------
# Empty voice id resolves to the default preset
# ---------------------------------------------------------------------------


def test_empty_voice_id_resolves_to_default_preset(tmp_path) -> None:
    engine, fake_model, modules = _ready_engine_and_mocks()
    out = tmp_path / "out.mp3"
    with patch.dict("sys.modules", modules), patch(
        "src.tts_qwen_common.combine_audio_files"
    ), patch(
        "src.tts_qwen_common.split_text_into_chunks", return_value=["hello"]
    ):
        engine.synthesize("hello", str(out), "", "en")
    instruct = fake_model.generate_voice_design.call_args.kwargs["instruct"]
    assert instruct == _VOICE_PRESETS[_DEFAULT_VOICE_ID][1]


# ---------------------------------------------------------------------------
# Multi-chunk wiring: combine args, wavs[0] selection, progress, ignored rate
# ---------------------------------------------------------------------------


def test_multichunk_wiring_progress_and_rate(tmp_path) -> None:
    engine, fake_model, modules = _ready_engine_and_mocks()
    # Distinguishable waveforms so wavs[0] selection (not [-1] or the whole
    # list) is actually pinned.
    fake_model.generate_voice_design.return_value = ([[1.0, 1.0], [2.0, 2.0]], 24000)

    captured: list = []

    def fake_write(path, wav, rate):
        captured.append(np.asarray(wav, dtype=np.float32))

    modules["soundfile"].write = fake_write

    events: list = []

    def progress(cur, total, msg):
        events.append((cur, total, msg))

    out = tmp_path / "out.mp3"
    with patch.dict("sys.modules", modules), patch(
        "src.tts_qwen_common.combine_audio_files"
    ) as fake_combine, patch(
        "src.tts_qwen_common.split_text_into_chunks",
        return_value=["chunk one", "chunk two"],
    ):
        engine.synthesize(
            "x", str(out), _DEFAULT_VOICE_ID, "en",
            progress_cb=progress, rate="+25%",  # rate must be silently ignored
        )

    # combine_audio_files received exactly the two chunk paths and the caller's
    # output_path (catches a dropped chunk or a misrouted destination).
    combine_args = fake_combine.call_args.args
    assert len(combine_args[0]) == 2
    assert combine_args[1] == str(out)

    # Every written waveform is the FIRST element of the returned list.
    assert len(captured) == 2
    assert all(np.allclose(w, [1.0, 1.0]) for w in captured)

    # Progress fired per chunk plus the combine/done milestones.
    synth_msgs = [m for _, _, m in events if "Synthesizing" in m]
    assert len(synth_msgs) == 2
    assert any("Combining" in m for _, _, m in events)
    assert any("Done" in m for _, _, m in events)


def test_empty_model_output_raises(tmp_path) -> None:
    engine, fake_model, modules = _ready_engine_and_mocks()
    fake_model.generate_voice_design.return_value = ([], 24000)  # no waveforms
    out = tmp_path / "out.mp3"
    with patch.dict("sys.modules", modules), patch(
        "src.tts_qwen_common.combine_audio_files"
    ), patch(
        "src.tts_qwen_common.split_text_into_chunks", return_value=["hello"]
    ):
        with pytest.raises(RuntimeError, match="no audio"):
            engine.synthesize("hello", str(out), _DEFAULT_VOICE_ID, "en")


# ---------------------------------------------------------------------------
# _to_cpu_float32_mono — the output coercion helper (the key robustness fix)
# ---------------------------------------------------------------------------


class _FakeTensor:
    """Minimal stand-in for a torch tensor: exposes the .detach().to().float()
    .numpy() chain the coercion relies on, recording the calls so the test can
    confirm the full chain ran."""

    def __init__(self, arr):
        self._arr = arr
        self.calls: list = []

    def detach(self):
        self.calls.append("detach")
        return self

    def to(self, device, **kwargs):
        self.calls.append(("to", device))
        return self

    def float(self):
        self.calls.append("float")
        return self

    def numpy(self):
        self.calls.append("numpy")
        return self._arr


class TestCoerceWaveform:
    def test_torch_tensor_branch_runs_full_chain(self) -> None:
        # A [1, N] payload behind the tensor interface — exercises the
        # detach/to-cpu/float/numpy path AND the [1, N] -> [N] flatten.
        t = _FakeTensor(np.array([[1.0, 2.0, 3.0]], dtype=np.float64))
        out = to_cpu_float32_mono(t)
        assert t.calls == ["detach", ("to", "cpu"), "float", "numpy"]
        assert out.dtype == np.float32
        assert out.ndim == 1
        assert out.tolist() == [1.0, 2.0, 3.0]

    def test_numpy_2d_is_flattened_and_cast(self) -> None:
        arr = np.array([[0.5, 0.25]], dtype=np.float64)  # [1, N], not a tensor
        out = to_cpu_float32_mono(arr)
        assert out.dtype == np.float32
        assert out.ndim == 1
        assert out.tolist() == [0.5, 0.25]

    def test_plain_list_is_coerced(self) -> None:
        out = to_cpu_float32_mono([0.0, 0.1, 0.0])
        assert out.dtype == np.float32
        assert out.ndim == 1
        assert out.tolist() == pytest.approx([0.0, 0.1, 0.0])


# ---------------------------------------------------------------------------
# Installer exclusion (brief constraint 3) — qwen_tts must stay out of the bundle
# ---------------------------------------------------------------------------


def _spec_excludes() -> set[str]:
    """Parse the `excludes = [...]` list literal out of audiobookmaker.spec."""
    import ast
    from pathlib import Path

    spec = Path(__file__).resolve().parent.parent / "audiobookmaker.spec"
    tree = ast.parse(spec.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "excludes" for t in node.targets
        ):
            return {
                el.value
                for el in node.value.elts
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            }
    return set()


def test_qwen_tts_excluded_from_installer() -> None:
    # Regression guard for constraint 3: a future edit that drops the exclude
    # (bundling torch into the end-user installer) must fail here.
    assert "qwen_tts" in _spec_excludes()


def test_dev_engines_not_registered_when_frozen() -> None:
    """Behavioral half of constraint 3: under ``sys.frozen`` the dev-only GPU
    engines (both Qwen engines + VoxCPM2) must NOT register, so they can never
    surface in a shipped installer build. The package-exclude test above only
    proves the qwen-tts *dependency* is unbundled — the adapter modules are
    plain ``src/*.py`` that ARE bundled and would register if the frozen gate in
    engine_registry were dropped.

    Run in a fresh subprocess: ``sys.frozen`` must be set before
    ``engine_registry`` is imported, and module caching makes an in-process
    reload unreliable.
    """
    import subprocess
    import sys as _sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    code = (
        "import sys\n"
        "sys.frozen = True\n"
        "import src.engine_registry\n"
        "from src.tts_base import registered_ids\n"
        "ids = registered_ids()\n"
        "assert 'qwen_voicedesign' not in ids, ids\n"
        "assert 'qwen_customvoice' not in ids, ids\n"
        "assert 'voxcpm2' not in ids, ids\n"
        "assert 'edge' in ids and 'piper' in ids, ids\n"
        "print('FROZEN_GATE_OK')\n"
    )
    result = subprocess.run(
        [_sys.executable, "-c", code],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "FROZEN_GATE_OK" in result.stdout, (result.stdout, result.stderr)


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
def test_real_synthesis_smoke(tmp_path) -> None:
    # Probe at runtime, not in a skipif decorator: a decorator condition is
    # evaluated at collection time, which would import torch and init a CUDA
    # context on a GPU box even for a fast mocked-only run.
    if not _gpu_and_qwen_available():
        pytest.skip("needs a CUDA GPU and `pip install qwen-tts`")
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
