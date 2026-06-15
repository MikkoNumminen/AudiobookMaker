"""Unit tests for scripts/generate_chatterbox_audiobook.py helpers.

These tests cover the state-reset, observability, and chunk-stats
helpers that were added to fight long-run drift (the "sentence endings
get swallowed after 4+ hours" bug). No torch, no CUDA, no chatterbox —
everything is mocked.
"""

from __future__ import annotations

import json
import re
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


class TestFinnishModelRevisionPin:
    def test_revision_is_a_pinned_commit_sha(self) -> None:
        """The Finnish model must be fetched at an immutable commit SHA, not a
        moving branch — otherwise an upstream rename of the exact T3/ref files
        silently breaks synthesis (the rotated-URL failure class)."""
        rev = gca.FINNISH_REVISION
        assert isinstance(rev, str)
        assert re.fullmatch(r"[0-9a-f]{40}", rev), (
            f"FINNISH_REVISION must be a 40-hex commit SHA, not {rev!r} "
            "(a branch like 'main' would defeat the pin)"
        )


class TestVadConstants:
    def test_tail_pad_is_larger_than_head_pad(self) -> None:
        """Quiet Finnish word endings need extra grace on the tail side."""
        assert gca.VAD_TAIL_PAD_MS > gca.VAD_HEAD_PAD_MS

    def test_fallback_trailing_threshold_is_more_negative(self) -> None:
        """More negative dB → quieter tails survive the trim."""
        assert gca.VAD_FALLBACK_TRAIL_DB < gca.VAD_FALLBACK_HEAD_DB

    def test_mid_join_keep_is_tighter_than_sentence_pads(self) -> None:
        """A mid-phrase join hard-caps silence to far less than a sentence
        end keeps — otherwise the force-split inside a phrase is a pause."""
        assert gca.MID_JOIN_TAIL_KEEP_MS < gca.VAD_TAIL_PAD_MS
        assert gca.MID_JOIN_HEAD_KEEP_MS < gca.VAD_HEAD_PAD_MS


class TestEndsOnPausePunct:
    """_ends_on_pause_punct is the boundary predicate: True at a real
    sentence/clause boundary, False at a mid-phrase force-split. It is now a
    thin wrapper over _seam_kind (the tiering lives there); this class keeps the
    closing-quote/bracket edge cases pinned."""

    @pytest.mark.parametrize("text", [
        "Tämä on lause.",
        "Onko näin?",
        "Varo!",
        "Hän mietti…",
        "ensin yksi asia,",          # clause boundary (comma)
        "seuraavasti:",              # colon
        "kaksi osaa;",               # semicolon
        "ajatus —",                  # em dash
        "toinen –",                  # en dash
        'hän sanoi "kyllä."',        # terminator behind a closing quote
        "(a footnote).",             # terminator behind a closing paren
        "summa (yhteensä),",         # comma behind a closing paren
    ])
    def test_punctuation_endings_warrant_a_pause(self, text: str) -> None:
        assert gca._ends_on_pause_punct(text) is True

    @pytest.mark.parametrize("text", [
        "midwordone",                # synthetic bare-word force-split endings
        "midwordtwo",
        "Capitalword",
        "lowerword",
        "force split mid phrase",
        "trailing spaces   ",
        "ends in a quote with no punct \"",
    ])
    def test_bare_word_endings_are_mid_phrase(self, text: str) -> None:
        assert gca._ends_on_pause_punct(text) is False

    def test_empty_is_not_a_pause(self) -> None:
        assert gca._ends_on_pause_punct("") is False
        assert gca._ends_on_pause_punct("   ") is False


class TestCapSilence:
    """_cap_trailing_silence / _cap_leading_silence collapse absolute silence
    at a mid-phrase join down to a small keep without clipping the speech.
    Pure pydub — no torch/silero needed."""

    @staticmethod
    def _tone(ms: int):
        from pydub.generators import Sine
        return Sine(220).to_audio_segment(duration=ms).apply_gain(-3)

    @staticmethod
    def _sil(ms: int):
        from pydub import AudioSegment
        return AudioSegment.silent(duration=ms)

    def test_trailing_silence_capped_to_keep(self) -> None:
        seg = self._tone(300) + self._sil(800)
        out = gca._cap_trailing_silence(seg, keep_ms=70)
        # 800ms trailing silence collapsed to ~70ms; tone (300ms) preserved.
        assert 360 <= len(out) <= 380

    def test_leading_silence_capped_to_keep(self) -> None:
        seg = self._sil(800) + self._tone(300)
        out = gca._cap_leading_silence(seg, keep_ms=40)
        assert 330 <= len(out) <= 350

    def test_short_silence_left_untouched(self) -> None:
        seg = self._tone(300) + self._sil(30)  # already shorter than keep
        out = gca._cap_trailing_silence(seg, keep_ms=70)
        assert len(out) == len(seg)

    def test_pure_speech_not_clipped(self) -> None:
        seg = self._tone(500)
        assert len(gca._cap_trailing_silence(seg, keep_ms=70)) == len(seg)
        assert len(gca._cap_leading_silence(seg, keep_ms=40)) == len(seg)


class TestSeamKind:
    """_seam_kind tiers the inter-chunk pause: sentence > clause > mid-word."""

    @pytest.mark.parametrize("text", [
        "Tämä on lause.",
        "Onko näin?",
        "Varo!",
        "Hän mietti…",
        '(a footnote).',          # terminator behind a closing paren
        'hän sanoi "kyllä."',     # terminator behind a closing quote
    ])
    def test_sentence_endings(self, text: str) -> None:
        assert gca._seam_kind(text) == "sentence"

    @pytest.mark.parametrize("text", [
        "ensin yksi asia,",       # comma
        "seuraavasti:",           # colon
        "kaksi osaa;",            # semicolon
        "ajatus —",               # em dash
        "toinen –",               # en dash
        "summa (yhteensä),",      # comma behind a closing paren
    ])
    def test_clause_endings(self, text: str) -> None:
        assert gca._seam_kind(text) == "clause"

    @pytest.mark.parametrize("text", [
        "midwordone", "midwordtwo", "force split mid phrase", "trailing spaces   ",
        "", "   ",
    ])
    def test_bare_words_are_mid(self, text: str) -> None:
        assert gca._seam_kind(text) == "mid"

    def test_gap_tiers_are_ordered(self) -> None:
        """A full stop pauses longer than a comma, which pauses longer than a
        mid-phrase rejoin (which adds no gap at all)."""
        assert gca._seam_gap_ms("End of sentence.") == gca.SENTENCE_SEAM_GAP_MS
        assert gca._seam_gap_ms("a clause,") == gca.CLAUSE_SEAM_GAP_MS
        assert gca._seam_gap_ms("bareword") == 0
        assert (
            gca._seam_gap_ms("bareword")
            < gca._seam_gap_ms("a clause,")
            < gca._seam_gap_ms("End of sentence.")
        )


class TestDocumentKind:
    """_document_kind labels a source by its real extension, not a hardcoded
    'PDF'. The non-EPUB branch parses PDF, DOCX and TXT through one PyMuPDF
    path, so the log must report whatever the file actually is."""

    @pytest.mark.parametrize("name,expected", [
        ("book.pdf", "PDF"),
        ("book.docx", "DOCX"),       # the case that mislabelled as "PDF"
        ("notes.txt", "TXT"),
        ("UPPER.PDF", "PDF"),        # extension casing is normalised
        ("dotted.name.docx", "DOCX"),  # only the final suffix counts
        ("no_extension", "document"),  # graceful fallback, never blank
    ])
    def test_label_follows_extension(self, name: str, expected: str) -> None:
        assert gca._document_kind(Path(name)) == expected


class TestHfHubWarningMuted:
    """The Hub server's "set a HF_TOKEN" advisory rides the `logging` channel
    on the huggingface_hub.utils._http logger; muting the parent namespace is
    the only thing that stops it (the warnings-module filter cannot reach a
    logging-module record). Importing the module runs the suppression block."""

    def test_parent_namespace_muted_to_error(self) -> None:
        import logging
        assert logging.getLogger("huggingface_hub").level == logging.ERROR

    def test_http_child_inherits_error(self) -> None:
        import logging
        # The advisory is emitted on the .utils._http child; with no explicit
        # level of its own it inherits ERROR from the parent we muted, so the
        # WARNING record is dropped before it can reach any handler.
        child = logging.getLogger("huggingface_hub.utils._http")
        assert child.getEffectiveLevel() == logging.ERROR


class TestCapInternalSilences:
    """_cap_internal_silences shortens an over-long pause in the MIDDLE of a
    chunk (the Finnish model renders 1–1.5s gaps at some punctuation) without
    touching the surrounding speech."""

    @staticmethod
    def _tone(ms: int):
        from pydub.generators import Sine
        return Sine(220).to_audio_segment(duration=ms).apply_gain(-3)

    @staticmethod
    def _sil(ms: int):
        from pydub import AudioSegment
        return AudioSegment.silent(duration=ms)

    def test_long_internal_silence_capped(self) -> None:
        seg = self._tone(300) + self._sil(1500) + self._tone(300)
        out = gca._cap_internal_silences(seg, max_ms=480)
        # 1500ms gap collapsed to ~480ms; both 300ms tones preserved.
        assert 1060 <= len(out) <= 1110

    def test_short_internal_silence_untouched(self) -> None:
        seg = self._tone(300) + self._sil(300) + self._tone(300)
        out = gca._cap_internal_silences(seg, max_ms=480)
        assert len(out) == len(seg)

    def test_multiple_long_silences_all_capped(self) -> None:
        seg = (self._tone(200) + self._sil(900) + self._tone(200)
               + self._sil(900) + self._tone(200))
        out = gca._cap_internal_silences(seg, max_ms=480)
        # two 900ms gaps → ~480ms each; speech (600ms) preserved.
        assert 1500 <= len(out) <= 1620

    def test_pure_speech_not_clipped(self) -> None:
        seg = self._tone(500)
        assert len(gca._cap_internal_silences(seg, max_ms=480)) == len(seg)


class TestAssembleChunks:
    """_assemble_chunks is the single source of the assembly pause logic: tiered
    seam gaps (sentence > clause > mid-word), internal-silence capping, and
    chapter-edge preservation. This is the integration guard that stops the
    long-pause bug from silently reappearing. Pure pydub — no torch/engine."""

    @staticmethod
    def _tone(ms: int):
        from pydub.generators import Sine
        return Sine(220).to_audio_segment(duration=ms).apply_gain(-3)

    @staticmethod
    def _sil(ms: int):
        from pydub import AudioSegment
        return AudioSegment.silent(duration=ms)

    def _seam_silence(self, left_text: str) -> int:
        """Assemble two interior chunks; return the single seam-silence length."""
        from pydub.silence import detect_silence
        a = self._tone(300) + self._sil(600)
        b = self._sil(600) + self._tone(300)
        out = gca._assemble_chunks([a, b], [left_text, "tail."])
        spans = detect_silence(out, min_silence_len=50, silence_thresh=-40)
        return max(e - s for s, e in spans)

    def test_sentence_seam_is_full(self) -> None:
        # ~70 trail + 370 gap + 40 lead
        assert 430 <= self._seam_silence("A full stop.") <= 540

    def test_clause_seam_is_medium(self) -> None:
        # ~70 trail + 150 gap + 40 lead
        assert 210 <= self._seam_silence("a clause,") <= 320

    def test_midword_seam_is_tight(self) -> None:
        # ~70 trail + 0 gap + 40 lead — a force-split inside a phrase
        assert 80 <= self._seam_silence("bareword") <= 170

    def test_seam_tiers_strictly_ordered(self) -> None:
        assert (
            self._seam_silence("End.")
            > self._seam_silence("mid,")
            > self._seam_silence("bareword")
        )

    def test_long_internal_pause_capped_in_assembly(self) -> None:
        # a 1.5s model pause in the MIDDLE of a chunk is shortened, not shipped
        from pydub.silence import detect_silence
        mid = self._tone(300) + self._sil(1500) + self._tone(300)
        out = gca._assemble_chunks([mid, self._tone(200)], ["one.", "two."])
        worst = max(e - s for s, e in detect_silence(out, min_silence_len=50, silence_thresh=-40))
        assert worst <= gca.MAX_INTERNAL_SILENCE_MS + gca.SILENCE_SCAN_STEP_MS

    def test_chapter_edges_preserved(self) -> None:
        # first chunk's lead-in and last chunk's tail are NOT seam-tightened
        from pydub.silence import detect_leading_silence
        first = self._sil(300) + self._tone(300)
        last = self._tone(300) + self._sil(300)
        out = gca._assemble_chunks([first, last], ["open.", "close."])
        assert detect_leading_silence(out, silence_threshold=-40) >= 200
        assert detect_leading_silence(out.reverse(), silence_threshold=-40) >= 200


class TestCachedChunkHealth:
    """_cached_chunk_healthy treats a too-short cached chunk as a miss so the
    runner re-synthesizes Chatterbox's early-stop truncations instead of
    shipping them as silent gaps ('pauses in the middle of a sentence')."""

    @staticmethod
    def _write_wav(path, seconds: float, rate: int = 24000) -> None:
        import wave
        n = int(seconds * rate)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"\x00\x00" * n)

    def test_truncated_chunk_is_unhealthy(self, tmp_path) -> None:
        p = tmp_path / "c.wav"
        self._write_wav(p, 0.9)            # 0.9s for 64 chars -> 0.014 s/char
        assert gca._cached_chunk_healthy(p, 64) is False

    def test_rambling_chunk_is_unhealthy(self, tmp_path) -> None:
        p = tmp_path / "c.wav"
        self._write_wav(p, 20.0)           # 20s for 64 chars -> 0.31 > 0.20
        assert gca._cached_chunk_healthy(p, 64) is False

    def test_sub_floor_rambling_chunk_is_unhealthy(self, tmp_path) -> None:
        # The exact bug this branch fixes: a tiny fragment that RAMBLES (12s
        # for 7 chars) must NOT be exempted just for being below the floor.
        p = tmp_path / "c.wav"
        self._write_wav(p, 12.0)
        assert gca._cached_chunk_healthy(p, 7) is False

    def test_sub_floor_brief_chunk_is_healthy(self, tmp_path) -> None:
        # A genuinely brief tiny sentence is fine — truncation is suppressed
        # below the floor; only the rambling edge is enforced there.
        p = tmp_path / "c.wav"
        self._write_wav(p, 1.0)
        assert gca._cached_chunk_healthy(p, 7) is True

    def test_full_chunk_is_healthy(self, tmp_path) -> None:
        p = tmp_path / "c.wav"
        self._write_wav(p, 12.0)           # 12s for 168 chars -> 0.071 s/char
        assert gca._cached_chunk_healthy(p, 168) is True

    def test_just_below_threshold_is_unhealthy(self, tmp_path) -> None:
        p = tmp_path / "c.wav"
        self._write_wav(p, 100 * (gca.MIN_AUDIO_S_PER_CHAR - 0.005))
        assert gca._cached_chunk_healthy(p, 100) is False

    def test_short_chunk_is_exempt(self, tmp_path) -> None:
        p = tmp_path / "c.wav"
        self._write_wav(p, 0.2)            # tiny audio, but text below the floor
        assert gca._cached_chunk_healthy(p, gca.MIN_AUDIO_RETRY_CHAR_FLOOR - 1) is True

    def test_missing_file_is_unhealthy(self, tmp_path) -> None:
        assert gca._cached_chunk_healthy(tmp_path / "nope.wav", 100) is False

    def test_cached_audio_seconds(self, tmp_path) -> None:
        p = tmp_path / "c.wav"
        self._write_wav(p, 2.0)
        assert 1.9 <= gca._cached_audio_seconds(p) <= 2.1


class TestRatioBadness:
    """_ratio_badness is 0 inside the healthy band and grows with distance
    outside it, so the retry loop can pick the least-bad attempt whether the
    failure is truncation (too short) or rambling (too long)."""

    def test_in_band_is_zero(self) -> None:
        assert gca._ratio_badness(5.0, 64) == 0.0          # 0.078 in band

    def test_truncation_has_positive_badness(self) -> None:
        assert gca._ratio_badness(0.9, 64) > 0             # 0.014 < MIN

    def test_rambling_has_positive_badness(self) -> None:
        assert gca._ratio_badness(20.0, 64) > 0            # 0.31 > MAX

    def test_sub_floor_truncation_is_suppressed(self) -> None:
        # A short, low-s/char tiny chunk is NOT flagged truncated (too noisy).
        assert gca._ratio_badness(0.1, 7) == 0.0

    def test_sub_floor_rambling_still_flagged(self) -> None:
        # ...but a tiny chunk that rambles IS flagged at any size.
        assert gca._ratio_badness(12.0, 7) > 0

    def test_less_truncated_attempt_is_preferred(self) -> None:
        assert gca._ratio_badness(2.0, 100) < gca._ratio_badness(1.0, 100)

    def test_less_rambling_attempt_is_preferred(self) -> None:
        assert gca._ratio_badness(25.0, 100) < gca._ratio_badness(40.0, 100)


# ---------------------------------------------------------------------------
# main(): a broken/drifted engine venv must produce an actionable repair
# message and exit code 2 — never a raw traceback.
# ---------------------------------------------------------------------------


class _RaisingFinder:
    """meta_path hook that makes ``import <target>`` raise the given error.

    Mirrors the _FakeFinder pattern used elsewhere in this file (find_module/
    load_module is deprecated but still honoured on the 3.11 runtime).
    """

    def __init__(self, target: str, exc: BaseException) -> None:
        self._target = target
        self._exc = exc

    def find_module(self, name, path=None):  # noqa: D401 - import hook protocol
        return self if name == self._target else None

    def load_module(self, name):
        raise self._exc


class TestMainEngineLoadFailure:
    def _run_main_with_failing_chatterbox(self, exc: BaseException, capsys):
        """Drive gca.main() with torch/torchaudio present but `import
        chatterbox` raising ``exc``; return (exit_code, stdout)."""
        finder = _RaisingFinder("chatterbox", exc)
        original_chatterbox = sys.modules.pop("chatterbox", None)
        sys.meta_path.insert(0, finder)
        try:
            with patch.dict(
                sys.modules, {"torch": MagicMock(), "torchaudio": MagicMock()}
            ), patch.object(
                gca, "parse_args",
                return_value=SimpleNamespace(dry_run=False, selftest=False),
            ):
                code = gca.main()
        finally:
            sys.meta_path.remove(finder)
            if original_chatterbox is not None:
                sys.modules["chatterbox"] = original_chatterbox
        return code, capsys.readouterr().out

    def test_version_drift_runtime_error_is_caught_and_actionable(self, capsys):
        """The transformers _LazyModule wrapper raises the LlamaModel failure
        as a RuntimeError, not an ImportError — the old `except ImportError`
        let it escape as a raw traceback. The broadened catch must map it to
        the repair message + exit 2 while preserving the raw signature."""
        exc = RuntimeError(
            "Could not import module 'LlamaModel'. Are this object's "
            "requirements defined correctly?"
        )
        code, out = self._run_main_with_failing_chatterbox(exc, capsys)
        assert code == 2
        assert "[error]" in out
        # Raw signature preserved in the log for diagnosis.
        assert "LlamaModel" in out
        # Actionable: points at the one-click (re)install and names the cause.
        assert "Install engines" in out
        assert "incompatible package versions" in out

    def test_missing_engine_importerror_is_still_handled(self, capsys):
        """A genuinely absent engine (plain ImportError) still maps to the
        same actionable message + exit 2."""
        exc = ImportError("No module named 'chatterbox'")
        code, out = self._run_main_with_failing_chatterbox(exc, capsys)
        assert code == 2
        assert "Install engines" in out


# ---------------------------------------------------------------------------
# --selftest + runner provenance stamp
# ---------------------------------------------------------------------------


class TestSelftest:
    def test_parse_args_accepts_selftest(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["gca", "--selftest"])
        args = gca.parse_args()
        assert args.selftest is True

    def test_main_dispatches_to_selftest_and_prints_stamp(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(sys, "argv", ["gca", "--selftest"])
        with patch.object(gca, "_selftest", return_value=0) as st:
            code = gca.main()
        st.assert_called_once()
        assert code == 0
        out = capsys.readouterr().out
        # Provenance: every run identifies WHICH copy of the script executed.
        assert f"[runner] build {gca.RUNNER_BUILD} @" in out

    def test_main_prints_stamp_on_normal_runs_too(self, monkeypatch, capsys):
        # No input args -> main exits early with the usage error, but the
        # stamp must already be on stdout (diagnosis works on every log).
        monkeypatch.setattr(sys, "argv", ["gca"])
        code = gca.main()
        assert code == 2
        out = capsys.readouterr().out
        assert "[runner] build" in out

    def test_selftest_failure_unmasks_chained_cause(self, monkeypatch, capsys):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "torch":
                raise RuntimeError(
                    "Could not import module 'LlamaModel'. Are this object's "
                    "requirements defined correctly?"
                )
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            code = gca._selftest()
        assert code == 2
        out = capsys.readouterr().out
        assert "[error]" in out
        assert "full selftest traceback" in out
        # The chained traceback is what reveals the REAL failure behind
        # transformers' masked message.
        assert "Traceback" in out

    def test_repo_root_is_appended_not_prepended(self):
        # Load-bearing one-liner: in a frozen install _REPO_ROOT is the app's
        # _internal bundle dir; PREPENDING it lets bundled packages shadow the
        # venv's torch/transformers for the synthesis subprocess. Guard the
        # source so the append can't silently regress to insert(0).
        source = (gca.Path(gca.__file__)).read_text(encoding="utf-8")
        assert "sys.path.append(str(_REPO_ROOT))" in source
        assert "sys.path.insert(0, str(_REPO_ROOT))" not in source
