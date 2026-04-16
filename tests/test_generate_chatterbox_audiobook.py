"""Unit tests for scripts/generate_chatterbox_audiobook.py helpers.

These tests cover the state-reset, observability, and chunk-stats
helpers that were added to fight long-run drift (the "sentence endings
get swallowed after 4+ hours" bug). No torch, no CUDA, no chatterbox —
everything is mocked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Make scripts/ importable as a sibling of src/.
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import generate_chatterbox_audiobook as gca  # type: ignore  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers for building a mock Chatterbox engine
# ---------------------------------------------------------------------------


def _make_mock_engine(
    n_layers: int = 30,
    with_hooks_on_layer_idxs: tuple[int, ...] = (),
) -> SimpleNamespace:
    """Build a minimal mock engine that mimics the attributes the real
    ChatterboxMultilingualTTS exposes and that _clear_chatterbox_state
    touches.
    """
    layers = []
    for i in range(n_layers):
        self_attn = SimpleNamespace()
        # Real torch modules expose _forward_hooks as an OrderedDict. For
        # our purposes a plain dict with a .clear() method is enough.
        hooks: dict = {}
        if i in with_hooks_on_layer_idxs:
            hooks[f"handle_{i}"] = object()
        self_attn._forward_hooks = hooks
        layers.append(SimpleNamespace(self_attn=self_attn))

    config = SimpleNamespace(
        output_attentions=True,         # mutated state that needs reset
        _attn_implementation="eager",   # mutated state that needs reset
    )
    tfmr = SimpleNamespace(layers=layers, config=config)
    t3 = SimpleNamespace(
        tfmr=tfmr,
        compiled=True,
        patched_model=SimpleNamespace(alignment_stream_analyzer="stale-ref"),
    )
    return SimpleNamespace(t3=t3, sr=24000)


# ---------------------------------------------------------------------------
# _clear_chatterbox_state
# ---------------------------------------------------------------------------


class TestClearChatterboxState:
    """The single most important function in the long-run loop. Every
    assertion here corresponds to a class of state leak that would
    otherwise accumulate across thousands of chunks."""

    def test_clears_forward_hooks_on_every_layer(self) -> None:
        engine = _make_mock_engine(
            n_layers=5,
            with_hooks_on_layer_idxs=(1, 2, 4),
        )
        gca._clear_chatterbox_state(engine)
        for layer in engine.t3.tfmr.layers:
            assert layer.self_attn._forward_hooks == {}

    def test_forces_compiled_false(self) -> None:
        engine = _make_mock_engine()
        assert engine.t3.compiled is True
        gca._clear_chatterbox_state(engine)
        assert engine.t3.compiled is False

    def test_drops_patched_model_reference(self) -> None:
        """If the previous patched_model stays referenced, its
        AlignmentStreamAnalyzer (and any CUDA tensors in its closure) is
        kept alive until the next generate() overwrites the field —
        which is too late in a long run."""
        engine = _make_mock_engine()
        assert engine.t3.patched_model is not None
        gca._clear_chatterbox_state(engine)
        assert engine.t3.patched_model is None

    def test_restores_config_to_canonical_defaults(self) -> None:
        """The analyzer flips these fields during construction and the
        upstream code re-saves the already-mutated values as 'originals'.
        We force them back to known-good values every call."""
        engine = _make_mock_engine()
        # Mutated state that the analyzer would leave behind.
        assert engine.t3.tfmr.config.output_attentions is True
        assert engine.t3.tfmr.config._attn_implementation == "eager"

        gca._clear_chatterbox_state(engine)

        assert engine.t3.tfmr.config.output_attentions is False
        assert engine.t3.tfmr.config._attn_implementation == "sdpa"

    def test_calls_gc_collect(self) -> None:
        """gc.collect() is what actually reclaims the just-dropped
        analyzer's closure — without it the CUDA tensors can linger."""
        engine = _make_mock_engine()
        with patch.object(gca, "gc", create=True):
            # gc is imported locally inside _clear_chatterbox_state, so
            # patch the import target instead.
            pass
        # Simpler: monkeypatch sys.modules' gc.
        import gc as real_gc
        with patch.object(real_gc, "collect") as mock_collect:
            gca._clear_chatterbox_state(engine)
        mock_collect.assert_called_once()

    def test_calls_torch_cuda_empty_cache_when_cuda_available(self) -> None:
        """empty_cache() releases the CUDA allocator's idle cached
        blocks. Without it the reserved-memory figure creeps upward
        over thousands of chunks."""
        engine = _make_mock_engine()
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        with patch.dict(sys.modules, {"torch": fake_torch}):
            gca._clear_chatterbox_state(engine)
        fake_torch.cuda.empty_cache.assert_called_once()

    def test_no_crash_when_cuda_unavailable(self) -> None:
        engine = _make_mock_engine()
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        with patch.dict(sys.modules, {"torch": fake_torch}):
            gca._clear_chatterbox_state(engine)
        fake_torch.cuda.empty_cache.assert_not_called()

    def test_no_crash_when_torch_not_importable(self) -> None:
        """The CPU-on-Mac dev path doesn't always have torch; we must
        fall through gracefully rather than break the synth loop."""
        engine = _make_mock_engine()

        original_torch = sys.modules.pop("torch", None)

        class _FakeFinder:
            """Make `import torch` raise ImportError for this test."""

            def find_module(self, name, path=None):
                return self if name == "torch" else None

            def load_module(self, name):
                raise ImportError("torch not installed (test shim)")

        sys.meta_path.insert(0, _FakeFinder())
        try:
            # Must not raise.
            gca._clear_chatterbox_state(engine)
        finally:
            sys.meta_path.pop(0)
            if original_torch is not None:
                sys.modules["torch"] = original_torch

    def test_no_crash_when_engine_shape_is_unexpected(self) -> None:
        """A stripped-down engine (e.g. a future Chatterbox version that
        removes .t3) must not break the synth loop. We swallow
        AttributeError defensively."""
        gca._clear_chatterbox_state(SimpleNamespace())
        gca._clear_chatterbox_state(SimpleNamespace(t3=SimpleNamespace()))

    def test_is_idempotent(self) -> None:
        engine = _make_mock_engine()
        gca._clear_chatterbox_state(engine)
        # A second call with already-cleaned state must not blow up.
        gca._clear_chatterbox_state(engine)
        assert engine.t3.compiled is False
        assert engine.t3.patched_model is None

    def test_repeated_calls_do_not_accumulate_hooks(self) -> None:
        """The bug this function fights: hooks accumulating across calls.
        We simulate 100 'generate' cycles and check hooks stay at zero
        between calls."""
        engine = _make_mock_engine(n_layers=5)

        def simulate_generate_registers_hooks():
            # Each "generate" would register 3 new hooks on 3 layers.
            engine.t3.tfmr.layers[0].self_attn._forward_hooks["h"] = object()
            engine.t3.tfmr.layers[2].self_attn._forward_hooks["h"] = object()
            engine.t3.tfmr.layers[4].self_attn._forward_hooks["h"] = object()

        for _ in range(100):
            simulate_generate_registers_hooks()
            gca._clear_chatterbox_state(engine)
            assert gca._chatterbox_hook_count(engine) == 0


# ---------------------------------------------------------------------------
# _chatterbox_hook_count
# ---------------------------------------------------------------------------


class TestHookCount:
    def test_sums_hooks_across_all_layers(self) -> None:
        engine = _make_mock_engine(
            n_layers=4,
            with_hooks_on_layer_idxs=(0, 2, 3),
        )
        assert gca._chatterbox_hook_count(engine) == 3

    def test_returns_zero_when_no_hooks(self) -> None:
        engine = _make_mock_engine(n_layers=30)
        assert gca._chatterbox_hook_count(engine) == 0

    def test_returns_sentinel_when_engine_shape_is_unexpected(self) -> None:
        assert gca._chatterbox_hook_count(SimpleNamespace()) == -1


# ---------------------------------------------------------------------------
# _gpu_mem_stats_mb
# ---------------------------------------------------------------------------


class TestGpuMemStats:
    def test_returns_empty_dict_when_torch_missing(self) -> None:
        original_torch = sys.modules.pop("torch", None)

        class _FakeFinder:
            def find_module(self, name, path=None):
                return self if name == "torch" else None

            def load_module(self, name):
                raise ImportError("no torch in test")

        sys.meta_path.insert(0, _FakeFinder())
        try:
            assert gca._gpu_mem_stats_mb() == {}
        finally:
            sys.meta_path.pop(0)
            if original_torch is not None:
                sys.modules["torch"] = original_torch

    def test_returns_empty_dict_when_cuda_unavailable(self) -> None:
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        with patch.dict(sys.modules, {"torch": fake_torch}):
            assert gca._gpu_mem_stats_mb() == {}

    def test_converts_bytes_to_mib(self) -> None:
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.memory_allocated.return_value = 2 * 1024 * 1024   # 2 MiB
        fake_torch.cuda.memory_reserved.return_value = 10 * 1024 * 1024   # 10 MiB
        with patch.dict(sys.modules, {"torch": fake_torch}):
            stats = gca._gpu_mem_stats_mb()
        assert stats == {"allocated_mb": 2.0, "reserved_mb": 10.0}


# ---------------------------------------------------------------------------
# _append_chunk_stats
# ---------------------------------------------------------------------------


class TestAppendChunkStats:
    def test_writes_one_json_line_per_record(self, tmp_path: Path) -> None:
        stats_path = tmp_path / ".chunk_stats.jsonl"
        gca._append_chunk_stats(stats_path, {"chunk": 1, "audio_s": 12.5})
        gca._append_chunk_stats(stats_path, {"chunk": 2, "audio_s": 13.1})

        lines = stats_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"chunk": 1, "audio_s": 12.5}
        assert json.loads(lines[1]) == {"chunk": 2, "audio_s": 13.1}

    def test_survives_unicode_in_payload(self, tmp_path: Path) -> None:
        stats_path = tmp_path / ".chunk_stats.jsonl"
        gca._append_chunk_stats(stats_path, {"title": "Kääntäjä äänikirja"})
        line = stats_path.read_text(encoding="utf-8").strip()
        assert json.loads(line) == {"title": "Kääntäjä äänikirja"}

    def test_swallows_os_errors_silently(self, tmp_path: Path) -> None:
        """Observability must never crash the synth loop. If the stats
        file can't be written for any reason, the synth loop keeps going."""
        # A directory path where a FILE is expected triggers OSError on open.
        bad_path = tmp_path / "a_directory_not_a_file"
        bad_path.mkdir()
        # Must not raise.
        gca._append_chunk_stats(bad_path, {"chunk": 1})


# ---------------------------------------------------------------------------
# Sanity: the VAD/trim constants from 571c761 are still in place.
# Not strictly related to the 4h-onset fix but ensures the silence-trim
# regression guard didn't get reverted.
# ---------------------------------------------------------------------------


class TestVadConstants:
    def test_tail_pad_is_larger_than_head_pad(self) -> None:
        """Quiet Finnish word endings need extra grace on the tail side."""
        assert gca.VAD_TAIL_PAD_MS > gca.VAD_HEAD_PAD_MS

    def test_fallback_trailing_threshold_is_more_negative(self) -> None:
        """More negative dB → quieter tails survive the trim."""
        assert gca.VAD_FALLBACK_TRAIL_DB < gca.VAD_FALLBACK_HEAD_DB
