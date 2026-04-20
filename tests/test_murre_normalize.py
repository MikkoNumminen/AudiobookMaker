"""Unit tests for src.murre_normalize.

The Murre runtime wrapper degrades to a no-op when the converted model
or the ``ctranslate2`` package is missing — that's the production state
on most dev machines today, and these tests assert it stays that way.
The chunking / dechunking helpers are tested in isolation so the
behavior is locked in regardless of whether a model is loaded.

For end-to-end coverage of the actual model, we mock
``ctranslate2.Translator`` so the test does not require the conversion
artifacts on disk.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src import murre_normalize as mn


@pytest.fixture(autouse=True)
def _reset_module_state() -> None:
    """Clear the module's global cache between tests."""
    mn.reset_for_tests()
    yield
    mn.reset_for_tests()


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_returns_false_when_marker_missing(self, tmp_path: Path) -> None:
        # No murre_ct2.json marker → unavailable, no exceptions.
        assert mn.is_available(model_dir=tmp_path) is False

    def test_returns_false_when_ctranslate2_missing(self, tmp_path: Path) -> None:
        # Marker present, but ctranslate2 import fails.
        (tmp_path / mn.DEFAULT_MARKER).write_text("{}", encoding="utf-8")
        with patch.dict("sys.modules", {"ctranslate2": None}):
            assert mn.is_available(model_dir=tmp_path) is False


# ---------------------------------------------------------------------------
# normalize() fallback behavior
# ---------------------------------------------------------------------------


class TestNormalizeFallback:
    def test_passes_through_when_unavailable(self, tmp_path: Path) -> None:
        text = "mä oon menos kauppaan"
        assert mn.normalize(text, model_dir=tmp_path) == text

    def test_empty_string_returns_empty(self, tmp_path: Path) -> None:
        assert mn.normalize("", model_dir=tmp_path) == ""

    def test_whitespace_only_returns_unchanged(self, tmp_path: Path) -> None:
        assert mn.normalize("   \n  ", model_dir=tmp_path) == "   \n  "

    def test_warns_only_once_when_missing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING", logger="src.murre_normalize"):
            mn.normalize("foo", model_dir=tmp_path)
            mn.normalize("bar", model_dir=tmp_path)
            mn.normalize("baz", model_dir=tmp_path)
        # Warning fires exactly once even after three normalize() calls.
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Chunking helpers (model-independent)
# ---------------------------------------------------------------------------


class TestChunking:
    def test_chunk_tokens_groups_of_three(self) -> None:
        result = mn._chunk_tokens(["a", "b", "c", "d", "e", "f"])
        assert result == [["a", "b", "c"], ["d", "e", "f"]]

    def test_chunk_tokens_short_tail(self) -> None:
        result = mn._chunk_tokens(["a", "b", "c", "d"])
        assert result == [["a", "b", "c"], ["d"]]

    def test_chunk_tokens_empty(self) -> None:
        assert mn._chunk_tokens([]) == []

    def test_chunk_tokens_invalid_size(self) -> None:
        with pytest.raises(ValueError):
            mn._chunk_tokens(["a"], chunk_size=0)

    def test_build_input_chunks_inserts_underscore_separators(self) -> None:
        result = mn._build_input_chunks(["mä", "oon", "menos"])
        assert result == [["mä", "_", "oon", "_", "menos"]]

    def test_build_input_chunks_handles_two_chunks(self) -> None:
        result = mn._build_input_chunks(["a", "b", "c", "d", "e"])
        assert result == [
            ["a", "_", "b", "_", "c"],
            ["d", "_", "e"],
        ]

    def test_build_input_chunks_empty_list(self) -> None:
        assert mn._build_input_chunks([]) == []


class TestDechunking:
    def test_dechunk_strips_underscore_separators(self) -> None:
        # The model emits the same `_` joiner tokens it saw on input.
        chunks = [["minä", "_", "olen", "_", "menossa"]]
        assert mn._dechunk(chunks) == "minä olen menossa"

    def test_dechunk_joins_multiple_chunks_with_space(self) -> None:
        chunks = [
            ["minä", "_", "olen", "_", "menossa"],
            ["kauppaan", "_", "tänään"],
        ]
        assert mn._dechunk(chunks) == "minä olen menossa kauppaan tänään"

    def test_dechunk_empty(self) -> None:
        assert mn._dechunk([]) == ""

    def test_dechunk_single_token_chunk(self) -> None:
        assert mn._dechunk([["sana"]]) == "sana"


# ---------------------------------------------------------------------------
# normalize() with mocked translator (end-to-end behavior, no real model)
# ---------------------------------------------------------------------------


class TestNormalizeWithMockedTranslator:
    def _make_marker(self, tmp_path: Path) -> Path:
        """Drop a marker file so is_available()/load() think a model exists."""
        marker = tmp_path / mn.DEFAULT_MARKER
        marker.write_text("{}", encoding="utf-8")
        return tmp_path

    def test_translates_simple_sentence(self, tmp_path: Path) -> None:
        model_dir = self._make_marker(tmp_path)

        # Mock ctranslate2.Translator.translate_batch to return a
        # deterministic "translated" hypothesis for each input chunk.
        fake_translator_cls = MagicMock()
        fake_translator = fake_translator_cls.return_value

        def _fake_translate_batch(chunks: list[list[str]]) -> list[Any]:
            results = []
            for chunk in chunks:
                # Pretend the model rewrites every "mä" to "minä" and
                # leaves everything else (including `_`) unchanged.
                hyp = ["minä" if t == "mä" else t for t in chunk]
                result = MagicMock()
                result.hypotheses = [hyp]
                results.append(result)
            return results

        fake_translator.translate_batch = _fake_translate_batch
        fake_module = MagicMock()
        fake_module.Translator = fake_translator_cls

        with patch.dict("sys.modules", {"ctranslate2": fake_module}):
            out = mn.normalize("mä syön", model_dir=model_dir)

        # "mä syön" → 1 chunk → ["mä", "_", "syön"] → translator rewrites
        # "mä" to "minä" → ["minä", "_", "syön"] → dechunk → "minä syön".
        assert out == "minä syön"

    def test_handles_empty_hypotheses_gracefully(self, tmp_path: Path) -> None:
        model_dir = self._make_marker(tmp_path)

        fake_translator_cls = MagicMock()
        fake_translator = fake_translator_cls.return_value

        def _empty_hyps(chunks: list[list[str]]) -> list[Any]:
            results = []
            for _ in chunks:
                r = MagicMock()
                r.hypotheses = []  # model emitted nothing
                results.append(r)
            return results

        fake_translator.translate_batch = _empty_hyps
        fake_module = MagicMock()
        fake_module.Translator = fake_translator_cls

        with patch.dict("sys.modules", {"ctranslate2": fake_module}):
            out = mn.normalize("yksi kaksi kolme", model_dir=model_dir)

        # All hypotheses empty → dechunk produces empty string.
        assert out == ""

    def test_load_failure_falls_back_silently(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        model_dir = self._make_marker(tmp_path)

        fake_translator_cls = MagicMock(side_effect=RuntimeError("bad model"))
        fake_module = MagicMock()
        fake_module.Translator = fake_translator_cls

        with patch.dict("sys.modules", {"ctranslate2": fake_module}):
            with caplog.at_level("WARNING", logger="src.murre_normalize"):
                out = mn.normalize("mä syön", model_dir=model_dir)

        # Translator init exploded → input passes through unchanged,
        # warning logged once.
        assert out == "mä syön"
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "bad model" in warnings[0].getMessage()
