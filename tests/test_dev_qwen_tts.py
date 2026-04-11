"""Unit tests for dev_qwen_tts.py.

dev_qwen_tts.py is a developer-only, single-file script at the repo
root. It is not installed as a package, so these tests have to jiggle
sys.path to import it. The heavy paths (torch, qwen_tts, snapshot
downloads, real synthesis) are never exercised — the tests only cover
the pure helpers that can be verified without a CUDA/MPS machine or
network access.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Make the repo root importable so `import dev_qwen_tts` works.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import dev_qwen_tts  # type: ignore  # noqa: E402
from dev_qwen_tts import (  # noqa: E402
    DEFAULT_PRESET_SPEAKER,
    MAX_CHUNK_CHARS,
    MAX_NEW_TOKENS,
    MODEL_BASE,
    MODEL_CUSTOM,
    MODEL_VOICEDESIGN,
    PRESET_SPEAKERS,
    QWEN_TTS_SPACE,
    SUPPORTED_LANGUAGES,
    language_warning,
    parse_args,
    pick_mode,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_model_ids_are_real_qwen_repos(self) -> None:
        assert MODEL_BASE.startswith("Qwen/Qwen3-TTS-")
        assert "Base" in MODEL_BASE
        assert MODEL_CUSTOM.startswith("Qwen/Qwen3-TTS-")
        assert "CustomVoice" in MODEL_CUSTOM
        assert MODEL_VOICEDESIGN.startswith("Qwen/Qwen3-TTS-")
        assert "VoiceDesign" in MODEL_VOICEDESIGN

    def test_hf_space_path_is_set(self) -> None:
        assert QWEN_TTS_SPACE == "Qwen/Qwen3-TTS"

    def test_max_chunk_chars_is_small_enough_for_qwen(self) -> None:
        # Qwen3-TTS struggles on long contexts; keep chunks under 1 kB.
        assert 100 <= MAX_CHUNK_CHARS <= 1000

    def test_max_new_tokens_is_reasonable(self) -> None:
        assert 256 <= MAX_NEW_TOKENS <= 8192

    def test_supported_languages_has_ten_entries(self) -> None:
        # Per the research agent: Qwen3-TTS only officially supports 10.
        assert len(SUPPORTED_LANGUAGES) == 10
        assert "English" in SUPPORTED_LANGUAGES
        assert "Chinese" in SUPPORTED_LANGUAGES

    def test_finnish_is_not_supported(self) -> None:
        # This is the whole point of the warning machinery.
        assert "Finnish" not in SUPPORTED_LANGUAGES

    def test_preset_speakers_are_non_empty(self) -> None:
        assert len(PRESET_SPEAKERS) >= 1

    def test_default_preset_speaker_is_in_preset_list(self) -> None:
        assert DEFAULT_PRESET_SPEAKER in PRESET_SPEAKERS


# ---------------------------------------------------------------------------
# pick_mode() — pure function
# ---------------------------------------------------------------------------


def _args(**overrides) -> argparse.Namespace:
    """Build an argparse Namespace with sensible defaults for tests."""
    defaults = {
        "pdf": "book.pdf",
        "voice_description": None,
        "ref_audio": None,
        "language": "English",
        "speaker": DEFAULT_PRESET_SPEAKER,
        "max_chunks": 0,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestPickMode:
    def test_no_flags_picks_customvoice(self) -> None:
        mode, model_id, dtype, label = pick_mode(_args())
        assert mode == "preset"
        assert model_id == MODEL_CUSTOM
        assert dtype == "float16"
        assert "CustomVoice" in label

    def test_voice_description_picks_voicedesign(self) -> None:
        mode, model_id, dtype, label = pick_mode(
            _args(voice_description="warm baritone elderly male")
        )
        assert mode == "design"
        assert model_id == MODEL_VOICEDESIGN
        assert dtype == "float16"
        assert "VoiceDesign" in label

    def test_ref_audio_picks_base_with_float32(self) -> None:
        mode, model_id, dtype, label = pick_mode(_args(ref_audio="/tmp/ref.wav"))
        assert mode == "clone"
        assert model_id == MODEL_BASE
        assert dtype == "float32", "cloning must use float32 on MPS"
        assert "Base" in label or "cloning" in label.lower()

    def test_ref_audio_wins_over_voice_description(self) -> None:
        # Precedence rule: --ref-audio beats --voice-description.
        mode, model_id, _, _ = pick_mode(
            _args(
                voice_description="deep male",
                ref_audio="/tmp/ref.wav",
            )
        )
        assert mode == "clone"
        assert model_id == MODEL_BASE


# ---------------------------------------------------------------------------
# language_warning() — pure function
# ---------------------------------------------------------------------------


class TestLanguageWarning:
    def test_supported_language_returns_none(self) -> None:
        assert language_warning("English") is None
        assert language_warning("Chinese") is None
        assert language_warning("German") is None

    def test_unsupported_language_returns_warning(self) -> None:
        w = language_warning("Finnish")
        assert w is not None
        assert "Finnish" in w
        assert "not" in w.lower()

    def test_warning_lists_supported_languages(self) -> None:
        w = language_warning("Klingon")
        assert w is not None
        # Users need to know which languages they *could* use.
        assert "English" in w
        assert "Chinese" in w


# ---------------------------------------------------------------------------
# parse_args() — CLI surface
# ---------------------------------------------------------------------------


def _parse(*argv: str) -> argparse.Namespace:
    with patch.object(sys, "argv", ["dev_qwen_tts.py", *argv]):
        return parse_args()


class TestParseArgs:
    def test_requires_pdf_positional(self) -> None:
        with patch.object(sys, "argv", ["dev_qwen_tts.py"]):
            with pytest.raises(SystemExit):
                parse_args()

    def test_minimal_invocation(self) -> None:
        args = _parse("book.pdf")
        assert args.pdf == "book.pdf"
        assert args.voice_description is None
        assert args.ref_audio is None
        assert args.language == "Finnish"
        assert args.speaker == DEFAULT_PRESET_SPEAKER
        assert args.max_chunks == 0

    def test_voice_description_flag(self) -> None:
        args = _parse("book.pdf", "--voice-description", "tired narrator")
        assert args.voice_description == "tired narrator"

    def test_ref_audio_flag(self) -> None:
        args = _parse("book.pdf", "--ref-audio", "my_voice.wav")
        assert args.ref_audio == "my_voice.wav"

    def test_language_flag(self) -> None:
        args = _parse("book.pdf", "--language", "English")
        assert args.language == "English"

    def test_max_chunks_flag(self) -> None:
        args = _parse("book.pdf", "--max-chunks", "3")
        assert args.max_chunks == 3

    def test_speaker_flag_accepts_preset(self) -> None:
        args = _parse("book.pdf", "--speaker", "Dylan")
        assert args.speaker == "Dylan"

    def test_speaker_flag_rejects_unknown(self) -> None:
        # argparse choices enforcement should kick in.
        with patch.object(
            sys, "argv", ["dev_qwen_tts.py", "book.pdf", "--speaker", "Morgan"]
        ):
            with pytest.raises(SystemExit):
                parse_args()

    def test_combined_flags(self) -> None:
        args = _parse(
            "book.pdf",
            "--ref-audio",
            "ref.wav",
            "--language",
            "English",
            "--max-chunks",
            "5",
        )
        assert args.pdf == "book.pdf"
        assert args.ref_audio == "ref.wav"
        assert args.language == "English"
        assert args.max_chunks == 5


# ---------------------------------------------------------------------------
# Module imports cleanly without heavy dependencies
# ---------------------------------------------------------------------------


def test_module_imports_without_torch_or_qwen() -> None:
    """Importing dev_qwen_tts must not pull in torch or qwen_tts.

    Top-level of the script should only touch stdlib + typing so --help
    stays instant and the test suite can run without a GPU/torch stack.
    """
    # If torch is installed on this machine the assertion below would
    # be meaningless. Instead, check that the dev_qwen_tts module does
    # NOT re-export torch attributes — i.e. its namespace does not
    # contain a `torch` symbol leaked at import time.
    assert not hasattr(dev_qwen_tts, "torch")
    assert not hasattr(dev_qwen_tts, "qwen_tts")
    assert not hasattr(dev_qwen_tts, "np")
    assert not hasattr(dev_qwen_tts, "sf")
