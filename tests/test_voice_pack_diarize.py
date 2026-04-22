"""Unit tests for :mod:`src.voice_pack.diarize`.

These tests never import pyannote, never touch a real HF token, and never
require a GPU. A fake pipeline is injected through the ``pipeline=`` kwarg.
"""

from __future__ import annotations

import logging
import sys
import types

import pytest

from src.voice_pack.diarize import _merge_adjacent, diarize, resolve_token
from src.voice_pack.types import DiarTurn


class _FakeSegment:
    def __init__(self, start: float, end: float) -> None:
        self.start = start
        self.end = end


class _FakeAnnotation:
    def __init__(self, tracks: list[tuple[_FakeSegment, int, str]]) -> None:
        self._tracks = tracks

    def itertracks(self, yield_label: bool = True):  # noqa: ARG002 - mirrors pyannote
        for seg, tid, label in self._tracks:
            yield seg, tid, label


class _FakePipeline:
    def __init__(self, annotation: _FakeAnnotation) -> None:
        self._a = annotation
        self.last_kwargs: dict = {}
        self.last_audio_path: str | None = None

    def __call__(self, audio_path, **kwargs):
        self.last_audio_path = audio_path
        self.last_kwargs = kwargs
        return self._a

    def to(self, device):  # noqa: ARG002 - pyannote API shim
        return self


def test_diarize_returns_sorted_turns(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    ann = _FakeAnnotation(
        [
            (_FakeSegment(2.0, 3.0), 0, "SPEAKER_01"),
            (_FakeSegment(0.0, 1.0), 0, "SPEAKER_00"),
            (_FakeSegment(1.0, 2.0), 0, "SPEAKER_00"),
        ]
    )
    out = diarize(audio, pipeline=_FakePipeline(ann))
    assert [(t.start, t.end, t.speaker) for t in out] == [
        (0.0, 2.0, "SPEAKER_00"),  # merged adjacent turns from same speaker
        (2.0, 3.0, "SPEAKER_01"),
    ]


def test_merge_adjacent_gap():
    turns = [
        DiarTurn(0.0, 1.0, "S0"),
        DiarTurn(1.05, 2.0, "S0"),  # gap 0.05 < 0.1 -> merge
        DiarTurn(2.3, 3.0, "S0"),  # gap 0.3 > 0.1 -> keep separate
    ]
    merged = _merge_adjacent(turns, gap_seconds=0.1)
    assert [(t.start, t.end, t.speaker) for t in merged] == [
        (0.0, 2.0, "S0"),
        (2.3, 3.0, "S0"),
    ]


def test_resolve_token_explicit():
    assert resolve_token("abc") == "abc"


def test_resolve_token_env(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    monkeypatch.setenv("HF_TOKEN", "t1")
    assert resolve_token(None) == "t1"


def test_resolve_token_env_fallback(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "t2")
    assert resolve_token(None) == "t2"


def test_resolve_token_missing(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="Hugging Face token"):
        resolve_token(None)


def test_diarize_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        diarize(tmp_path / "nope.wav", pipeline=_FakePipeline(_FakeAnnotation([])))


class _BrokenModule:
    """Stand-in sys.modules entry whose attribute access blows up.

    Drives the ``except Exception: continue`` branch inside the HF-token
    shim's sys.modules sweep.
    """

    __name__ = "fake.broken.module"

    def __getattribute__(self, name):  # noqa: D401 - simple override
        if name == "__name__":
            return "fake.broken.module"
        raise RuntimeError(f"broken attribute access: {name}")


def test_hf_token_shim_logs_when_module_patch_fails(monkeypatch, caplog):
    """Installed modules that raise on attribute access must not silently
    break the shim — a debug log per skipped module now records why."""
    from src.voice_pack import diarize as diarize_mod

    # Install a minimal fake ``huggingface_hub`` so the shim can import it
    # without pulling the real (optional) dependency.
    fake_hub = types.ModuleType("huggingface_hub")
    fake_hub.hf_hub_download = lambda *a, **k: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)

    # Plant a module that explodes when the shim does ``getattr(mod, ...)``.
    broken = _BrokenModule()
    monkeypatch.setitem(sys.modules, "fake.broken.module", broken)

    # Reset the idempotency flag so the shim actually runs under the fake.
    monkeypatch.setattr(diarize_mod, "_HF_TOKEN_SHIM_APPLIED", False)

    with caplog.at_level(logging.DEBUG, logger="src.voice_pack.diarize"):
        diarize_mod._apply_hf_token_shim()

    assert any(
        "HF token patch skipped" in record.getMessage()
        and "fake.broken.module" in record.getMessage()
        for record in caplog.records
    )
