"""Tests for :mod:`src.voice_pack.emotion`.

These tests use dependency injection to avoid pulling in speechbrain,
pydub, or real audio. Only numpy + torch are imported, which are already
available in the test env via Chatterbox deps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from src.voice_pack.emotion import _resample_linear, tag_emotions  # noqa: E402
from src.voice_pack.types import VoiceChunk  # noqa: E402


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeClassifier:
    """Minimal stand-in for a SpeechBrain emotion classifier."""

    def __init__(self, label: str = "neu", prob: float = 0.87) -> None:
        self._label = label
        self._prob = prob
        self.call_count = 0
        self.last_samples: Any = None

    def classify_batch(self, batch: Any):
        self.call_count += 1
        if hasattr(batch, "cpu"):
            self.last_samples = batch.squeeze(0).cpu().numpy()
        else:
            self.last_samples = batch.squeeze(0)
        out_prob = torch.tensor([[self._prob, 1 - self._prob, 0.0, 0.0]])
        return (
            out_prob,
            torch.tensor([self._prob]),
            torch.tensor([0]),
            [self._label],
        )


def _fake_loader(sr: int = 16000, duration_s: float = 5.0, channels: int = 1):
    """Build an audio_loader callable that returns zeros of the given shape."""

    def _loader(_path: Any):
        n = int(sr * duration_s)
        if channels == 1:
            arr = np.zeros((n,), dtype=np.float32)
        else:
            arr = np.zeros((n, channels), dtype=np.float32)
        return arr, sr

    return _loader


def _make_chunk(start: float, end: float, text: str = "hello", speaker: str = "S0", confidence: float = 0.9) -> VoiceChunk:
    return VoiceChunk(start=start, end=end, text=text, speaker=speaker, confidence=confidence)


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    """A real (empty) file on disk so the existence check passes."""
    p = tmp_path / "dummy.wav"
    p.write_bytes(b"not really audio")
    return p


# ---------------------------------------------------------------------------
# tag_emotions
# ---------------------------------------------------------------------------


def test_tag_emotions_empty_list(audio_file: Path) -> None:
    loader = MagicMock()
    model = _FakeClassifier()
    out = tag_emotions([], audio_file, model=model, audio_loader=loader)
    assert out == []
    loader.assert_not_called()
    assert model.call_count == 0


@pytest.mark.parametrize(
    "short,canonical",
    [("ang", "angry"), ("hap", "happy"), ("sad", "sad"), ("neu", "neutral")],
)
def test_tag_emotions_normalises_labels(audio_file: Path, short: str, canonical: str) -> None:
    chunks = [_make_chunk(0.0, 2.0)]
    model = _FakeClassifier(label=short, prob=0.77)
    out = tag_emotions(chunks, audio_file, model=model, audio_loader=_fake_loader())
    assert len(out) == 1
    assert out[0].emotion == canonical
    assert out[0].emotion_confidence == pytest.approx(0.77)


def test_tag_emotions_unknown_label(audio_file: Path) -> None:
    """Unknown labels map to ``"unknown"`` with confidence zeroed.

    We intentionally drop the classifier's confidence for unknown labels:
    downstream rebalancing treats unknown as skip, so a nonzero confidence
    would be misleading.
    """
    chunks = [_make_chunk(0.0, 2.0)]
    model = _FakeClassifier(label="xyz", prob=0.99)
    out = tag_emotions(chunks, audio_file, model=model, audio_loader=_fake_loader())
    assert out[0].emotion == "unknown"
    assert out[0].emotion_confidence == 0.0


def test_tag_emotions_preserves_chunk_identity(audio_file: Path) -> None:
    chunks = [
        _make_chunk(0.0, 1.5, text="first", speaker="A", confidence=0.8),
        _make_chunk(1.5, 3.0, text="second", speaker="B", confidence=0.7),
        _make_chunk(3.0, 4.2, text="third", speaker="A", confidence=0.95),
    ]
    model = _FakeClassifier(label="neu", prob=0.5)
    out = tag_emotions(chunks, audio_file, model=model, audio_loader=_fake_loader())
    assert len(out) == len(chunks)
    for original, tagged in zip(chunks, out):
        assert tagged.start == original.start
        assert tagged.end == original.end
        assert tagged.text == original.text
        assert tagged.speaker == original.speaker
        assert tagged.confidence == original.confidence


def test_tag_emotions_skips_too_short_slice(audio_file: Path) -> None:
    """Slices shorter than 100 ms never reach the classifier."""
    chunks = [
        _make_chunk(0.0, 0.05, text="tiny"),  # 50 ms — too short
        _make_chunk(1.0, 2.5, text="ok"),      # 1.5 s — fine
    ]
    model = _FakeClassifier(label="ang", prob=0.6)
    out = tag_emotions(chunks, audio_file, model=model, audio_loader=_fake_loader())

    assert out[0].emotion == "unknown"
    assert out[0].emotion_confidence == 0.0
    assert out[1].emotion == "angry"
    assert out[1].emotion_confidence == pytest.approx(0.6)
    # Classifier called exactly once — for the second chunk only.
    assert model.call_count == 1


def test_tag_emotions_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.wav"
    chunks = [_make_chunk(0.0, 2.0)]
    model = _FakeClassifier()
    loader = MagicMock()
    with pytest.raises(FileNotFoundError):
        tag_emotions(chunks, missing, model=model, audio_loader=loader)
    loader.assert_not_called()
    assert model.call_count == 0


def test_tag_emotions_mono_stereo_reduction(audio_file: Path) -> None:
    """Stereo input is averaged to mono before reaching the classifier."""
    sr = 16000
    duration_s = 2.0
    n = int(sr * duration_s)

    def stereo_loader(_path: Any):
        # Distinct per-channel values so averaging is observable.
        arr = np.zeros((n, 2), dtype=np.float32)
        arr[:, 0] = 0.5
        arr[:, 1] = -0.5
        return arr, sr

    chunks = [_make_chunk(0.0, duration_s)]
    model = _FakeClassifier(label="neu", prob=0.9)
    tag_emotions(chunks, audio_file, model=model, audio_loader=stereo_loader)

    assert model.last_samples is not None
    received = np.asarray(model.last_samples)
    assert received.ndim == 1
    assert received.shape[0] == n
    # Average of +0.5 and -0.5 is zero.
    assert np.allclose(received, 0.0)


# ---------------------------------------------------------------------------
# _resample_linear
# ---------------------------------------------------------------------------


def test_resample_linear_noop() -> None:
    samples = np.array([0.1, -0.2, 0.3, 0.4, -0.5], dtype=np.float32)
    out = _resample_linear(samples, 16000, 16000)
    assert out is samples or np.array_equal(out, samples)


def test_resample_linear_halves() -> None:
    samples = np.linspace(-1.0, 1.0, num=16000, dtype=np.float32)
    out = _resample_linear(samples, 16000, 8000)
    # Allow a one-sample rounding tolerance.
    assert abs(out.shape[0] - 8000) <= 1
