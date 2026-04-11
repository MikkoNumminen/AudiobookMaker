"""Unit tests for dev_chatterbox_fi.py.

dev_chatterbox_fi.py is a developer-only, single-file script at the
repo root. It is not installed as a package, so these tests have to
jiggle sys.path to import it. The heavy paths (torch, chatterbox,
safetensors, snapshot downloads, real synthesis) are never exercised —
the tests only cover the pure helpers that can be verified without a
CUDA/MPS machine, a GPU, or network access.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Make the repo root importable so `import dev_chatterbox_fi` works.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import dev_chatterbox_fi  # type: ignore  # noqa: E402
from dev_chatterbox_fi import (  # noqa: E402
    DEFAULT_SENTENCE,
    FI_CFG_WEIGHT,
    FI_EXAGGERATION,
    FI_REPETITION_PENALTY,
    FI_TEMPERATURE,
    FINNISH_REF_WAV,
    FINNISH_REPO,
    FINNISH_T3_FILE,
    normalize_finnish_text,
    parse_args,
    pick_text,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_default_sentence_is_non_empty_finnish(self) -> None:
        assert isinstance(DEFAULT_SENTENCE, str)
        assert len(DEFAULT_SENTENCE) > 0
        # Sanity: the fallback should actually be Finnish — look for
        # characteristic diacritics and a Finnish keyword.
        lowered = DEFAULT_SENTENCE.lower()
        assert "ä" in lowered or "ö" in lowered
        assert "chatterbox" in lowered or "suomen" in lowered

    def test_finnish_repo_is_finnish_nlp_chatterbox(self) -> None:
        assert FINNISH_REPO == "Finnish-NLP/Chatterbox-Finnish"

    def test_finnish_t3_file_is_safetensors(self) -> None:
        assert FINNISH_T3_FILE.endswith(".safetensors")

    def test_finnish_ref_wav_is_wav(self) -> None:
        assert FINNISH_REF_WAV.endswith(".wav")

    def test_fi_repetition_penalty_matches_model_card(self) -> None:
        # Finnish "Golden Settings" from the HF model card.
        assert FI_REPETITION_PENALTY == 1.5

    def test_fi_temperature_matches_model_card(self) -> None:
        assert FI_TEMPERATURE == 0.8

    def test_fi_exaggeration_matches_model_card(self) -> None:
        assert FI_EXAGGERATION == 0.5

    def test_fi_cfg_weight_matches_model_card(self) -> None:
        assert FI_CFG_WEIGHT == 0.3


# ---------------------------------------------------------------------------
# parse_args() — CLI surface
# ---------------------------------------------------------------------------


def _parse(*argv: str) -> argparse.Namespace:
    with patch.object(sys, "argv", ["dev_chatterbox_fi.py", *argv]):
        return parse_args()


class TestParseArgs:
    def test_minimal_invocation_defaults(self) -> None:
        args = _parse()
        assert args.device == "cpu"
        assert args.text is None
        assert args.pdf is None
        assert args.finnish_finetune is False
        assert args.chunks == 1
        # Default lowered from 500 → 300 after upstream community feedback:
        # Chatterbox hallucinates on chunks >~300 chars (issues #60, #424).
        assert args.chunk_chars == 300

    def test_device_cpu_accepted(self) -> None:
        args = _parse("--device", "cpu")
        assert args.device == "cpu"

    def test_device_mps_accepted(self) -> None:
        args = _parse("--device", "mps")
        assert args.device == "mps"

    def test_device_cuda_accepted(self) -> None:
        args = _parse("--device", "cuda")
        assert args.device == "cuda"

    def test_device_garbage_rejected(self) -> None:
        # argparse choices enforcement should kick in.
        with patch.object(
            sys, "argv", ["dev_chatterbox_fi.py", "--device", "garbage"]
        ):
            with pytest.raises(SystemExit):
                parse_args()

    def test_text_flag(self) -> None:
        args = _parse("--text", "hello")
        assert args.text == "hello"

    def test_pdf_flag(self) -> None:
        args = _parse("--pdf", "book.pdf")
        assert args.pdf == "book.pdf"

    def test_finnish_finetune_flag(self) -> None:
        args = _parse("--finnish-finetune")
        assert args.finnish_finetune is True

    def test_chunks_flag(self) -> None:
        args = _parse("--chunks", "4")
        assert args.chunks == 4

    def test_chunk_chars_flag(self) -> None:
        args = _parse("--chunk-chars", "400")
        assert args.chunk_chars == 400

    def test_output_flag(self) -> None:
        args = _parse("--output", "out.mp3")
        assert args.output == "out.mp3"

    def test_ref_audio_flag(self) -> None:
        args = _parse("--ref-audio", "my.wav")
        assert args.ref_audio == "my.wav"

    def test_combined_flags(self) -> None:
        args = _parse(
            "--device",
            "mps",
            "--text",
            "hei maailma",
            "--chunks",
            "3",
            "--chunk-chars",
            "250",
            "--finnish-finetune",
        )
        assert args.device == "mps"
        assert args.text == "hei maailma"
        assert args.chunks == 3
        assert args.chunk_chars == 250
        assert args.finnish_finetune is True


# ---------------------------------------------------------------------------
# pick_text() — pure branches (no PDF fixture needed)
# ---------------------------------------------------------------------------


def _args(**overrides) -> argparse.Namespace:
    """Build an argparse Namespace with sensible defaults for tests."""
    defaults = {
        "text": None,
        "pdf": None,
        "chunks": 1,
        "chunk_chars": 300,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestPickText:
    def test_explicit_text_returns_single_element_list(self) -> None:
        result = pick_text(_args(text="Oma lauseeni."))
        assert result == ["Oma lauseeni."]

    def test_explicit_text_wraps_even_long_strings(self) -> None:
        # pick_text should not chunk a user-supplied --text; the user
        # is in charge of sizing when they pass it explicitly.
        long_text = "a" * 5000
        result = pick_text(_args(text=long_text))
        assert result == [long_text]
        assert len(result) == 1

    def test_no_text_no_pdf_returns_default_sentence(self) -> None:
        result = pick_text(_args())
        assert result == [DEFAULT_SENTENCE]

    def test_invalid_pdf_path_does_not_crash(self) -> None:
        # pick_text wraps parse_pdf inside the `if args.pdf:` branch.
        # With a bogus path the parser will fail; the code may either
        # fall back to DEFAULT_SENTENCE or raise. We accept both as
        # long as it doesn't silently hang or return gibberish.
        try:
            result = pick_text(_args(pdf="/nonexistent/definitely_not_a.pdf"))
        except Exception:
            # Raising on a bad PDF path is acceptable behavior.
            return
        # If it returned, it must be a non-empty list of strings.
        assert isinstance(result, list)
        assert len(result) >= 1
        assert all(isinstance(chunk, str) and chunk for chunk in result)

    def test_text_beats_pdf(self) -> None:
        # When both --text and --pdf are supplied, --text wins and
        # pick_text must not try to open the PDF at all.
        result = pick_text(
            _args(text="wins", pdf="/nonexistent/should_not_be_read.pdf")
        )
        assert result == ["wins"]


# ---------------------------------------------------------------------------
# normalize_finnish_text() — Finnish number/abbreviation expansion
# ---------------------------------------------------------------------------


# Skip the whole normalizer class if num2words isn't importable — without it
# the normalizer is a no-op by design and the assertions below would fail.
num2words = pytest.importorskip("num2words")  # noqa: F401


_DIGIT_RE = __import__("re").compile(r"\d")


class TestNormalizeFinnishText:
    def test_plain_year_expands_without_digits(self) -> None:
        result = normalize_finnish_text("1500")
        assert not _DIGIT_RE.search(result), f"still has digits: {result!r}"
        assert result.strip() != ""

    def test_century_expression_keeps_suffix(self) -> None:
        result = normalize_finnish_text("1500-luvulla")
        assert "luvulla" in result
        assert not _DIGIT_RE.search(result)

    def test_century_variants(self) -> None:
        for suffix in ("luvun", "luvulta", "luvulle", "luku"):
            inp = f"1800-{suffix}"
            out = normalize_finnish_text(inp)
            assert suffix in out, f"{inp!r} lost suffix: {out!r}"
            assert not _DIGIT_RE.search(out), f"{inp!r} kept digits: {out!r}"

    def test_numeric_range_expands_to_space_separated_words(self) -> None:
        result = normalize_finnish_text("1500-1800")
        assert not _DIGIT_RE.search(result)
        # Must be two space-separated expansions (not glued together).
        assert " " in result.strip()

    def test_page_abbreviation_becomes_sivu(self) -> None:
        result = normalize_finnish_text("s. 42")
        assert "sivu" in result
        assert not _DIGIT_RE.search(result)

    def test_elided_hyphen_gets_space(self) -> None:
        result = normalize_finnish_text("keski-ja uuden ajan")
        assert "keski- ja" in result

    def test_plain_text_unchanged(self) -> None:
        src = "Tämä on tavallinen suomenkielinen lause ilman numeroita."
        assert normalize_finnish_text(src) == src

    def test_empty_string(self) -> None:
        assert normalize_finnish_text("") == ""

    def test_integration_realistic_sentence(self) -> None:
        src = "1500-luvun alussa s. 42 (Mattila 1999) käytti 1,5 prosenttia."
        out = normalize_finnish_text(src)
        assert not _DIGIT_RE.search(out), f"digits survived: {out!r}"
        # Century suffix kept.
        assert "luvun" in out
        # Page abbreviation expanded.
        assert "sivu" in out
        # Bibliographic citation dropped.
        assert "Mattila" not in out
        # Decimal expanded — there must be something between käytti and
        # prosenttia that isn't a digit.
        assert "käytti" in out and "prosenttia" in out

    def test_compound_numbers_get_spaces(self) -> None:
        # num2words 0.5.14 glues Finnish compound-number morphemes
        # together (e.g. 1889 -> "kahdeksansataakahdeksankymmentäyhdeksän").
        # Post-processor must insert spaces at morpheme boundaries so
        # Chatterbox-TTS tokenizes and pronounces them correctly.
        out = normalize_finnish_text("1889")
        assert " " in out
        assert "kahdeksansataa kahdeksankymmentä" in out
        assert "kahdeksankymmentä yhdeksän" in out
        assert "kahdeksansataakahdeksankymmentäyhdeksän" not in out

        # Two-digit compound.
        assert normalize_finnish_text("42") == "neljäkymmentä kaksi"

        # Hundreds + teens.
        assert "yhdeksänsataa seitsemäntoista" in normalize_finnish_text("1917")

        # Standalone teens must NOT be split.
        assert normalize_finnish_text("15") == "viisitoista"
        assert normalize_finnish_text("11") == "yksitoista"

        # Clean hundreds must not get a spurious space.
        assert normalize_finnish_text("1500") == "tuhat viisisataa"

    def test_decimal_comma_expansion(self) -> None:
        out = normalize_finnish_text("1,5")
        assert not _DIGIT_RE.search(out)
        assert out.strip() != ""

    def test_num2words_missing_returns_input(self, monkeypatch) -> None:
        """If num2words is not installed, the function must no-op."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "num2words":
                raise ImportError("simulated missing num2words")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        src = "1500-luvulla käytettiin 42:ta."
        assert normalize_finnish_text(src) == src


# ---------------------------------------------------------------------------
# Module imports cleanly without heavy dependencies
# ---------------------------------------------------------------------------


def test_module_imports_without_torch_or_chatterbox() -> None:
    """Importing dev_chatterbox_fi must not pull in torch or chatterbox.

    Top-level of the script should only touch stdlib + typing so --help
    stays instant and the test suite can run without a GPU/torch stack.
    The heavy imports (torch, chatterbox, safetensors, torchaudio) live
    inside main() on purpose.
    """
    # If torch is installed on this machine the assertion below would
    # be meaningless. Instead, check that the dev_chatterbox_fi module
    # does NOT re-export torch attributes — i.e. its namespace does not
    # contain a `torch` symbol leaked at import time.
    assert not hasattr(dev_chatterbox_fi, "torch")
    assert not hasattr(dev_chatterbox_fi, "chatterbox")
    assert not hasattr(dev_chatterbox_fi, "np")
    assert not hasattr(dev_chatterbox_fi, "sf")
