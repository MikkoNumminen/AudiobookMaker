"""Unit tests for :mod:`src.voice_pack.asr`.

These tests never touch the real ``faster-whisper`` package — they inject
a fake model with the same ``.transcribe()`` shape so the suite can run
on any machine (no GPU, no optional dependencies).
"""

from __future__ import annotations

import builtins

import pytest

from src.voice_pack.asr import transcribe
from src.voice_pack.types import AsrSegment


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str, avg_logprob: float | None = -0.1) -> None:
        self.start = start
        self.end = end
        self.text = text
        self.avg_logprob = avg_logprob


class _FakeInfo:
    def __init__(self, language: str = "en") -> None:
        self.language = language


class _FakeModel:
    def __init__(self, segments: list[_FakeSegment]) -> None:
        self._segments = segments
        self.last_kwargs: dict | None = None

    def transcribe(self, audio, **kwargs):
        self.last_kwargs = kwargs
        return iter(self._segments), _FakeInfo()


def _make_audio(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")  # fake model ignores contents
    return audio


def test_transcribe_returns_asr_segments(tmp_path):
    audio = _make_audio(tmp_path)
    fake = _FakeModel(
        [
            _FakeSegment(0.0, 1.0, " hello ", -0.1),
            _FakeSegment(1.0, 2.0, "world", -0.5),
        ]
    )

    out = transcribe(audio, model=fake)

    assert len(out) == 2
    assert out[0] == AsrSegment(start=0.0, end=1.0, text="hello", confidence=pytest.approx(0.9))
    assert out[1].confidence == pytest.approx(0.5)
    assert out[1].text == "world"


def test_transcribe_drops_empty_text_segments(tmp_path):
    audio = _make_audio(tmp_path)
    fake = _FakeModel(
        [
            _FakeSegment(0.0, 1.0, "   ", -0.1),       # whitespace only
            _FakeSegment(1.0, 2.0, "", -0.1),           # empty
            _FakeSegment(2.0, 3.0, "keep me", -0.1),
            _FakeSegment(3.0, 4.0, "\n\t", -0.1),       # other whitespace
        ]
    )

    out = transcribe(audio, model=fake)

    assert len(out) == 1
    assert out[0].text == "keep me"
    assert out[0].start == 2.0
    assert out[0].end == 3.0


def test_transcribe_none_avg_logprob_gives_confidence_one(tmp_path):
    audio = _make_audio(tmp_path)
    fake = _FakeModel([_FakeSegment(0.0, 1.0, "hi", avg_logprob=None)])

    out = transcribe(audio, model=fake)

    assert len(out) == 1
    assert out[0].confidence == pytest.approx(1.0)


def test_transcribe_clamps_very_negative_logprob_to_zero(tmp_path):
    audio = _make_audio(tmp_path)
    fake = _FakeModel([_FakeSegment(0.0, 1.0, "hi", avg_logprob=-5.0)])

    out = transcribe(audio, model=fake)

    assert out[0].confidence == pytest.approx(0.0)


def test_transcribe_missing_audio_raises_file_not_found(tmp_path):
    missing = tmp_path / "does_not_exist.wav"
    fake = _FakeModel([_FakeSegment(0.0, 1.0, "hello", -0.1)])

    with pytest.raises(FileNotFoundError):
        transcribe(missing, model=fake)


def test_transcribe_checks_existence_before_calling_model(tmp_path):
    """The existence check must fire before we touch the model — that way
    a missing file fails cleanly even if faster-whisper isn't installed."""
    missing = tmp_path / "nope.wav"

    class _ExplodingModel:
        def transcribe(self, *args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("model.transcribe() should not be called for missing files")

    with pytest.raises(FileNotFoundError):
        transcribe(missing, model=_ExplodingModel())


def test_transcribe_accepts_string_path(tmp_path):
    audio = _make_audio(tmp_path)
    fake = _FakeModel([_FakeSegment(0.0, 1.0, "hi", -0.1)])

    out = transcribe(str(audio), model=fake)

    assert len(out) == 1


def test_transcribe_forwards_language_and_beam(tmp_path):
    audio = _make_audio(tmp_path)
    fake = _FakeModel([_FakeSegment(0.0, 1.0, "hei", -0.1)])

    transcribe(audio, model=fake, language="fi", vad_filter=False, beam_size=3)

    assert fake.last_kwargs is not None
    assert fake.last_kwargs["language"] == "fi"
    assert fake.last_kwargs["vad_filter"] is False
    assert fake.last_kwargs["beam_size"] == 3


def test_transcribe_without_model_raises_import_error_when_package_missing(tmp_path, monkeypatch):
    """If the caller doesn't pre-load a model and faster-whisper isn't
    installed, we must raise a clear ImportError pointing at the pip
    package."""
    audio = _make_audio(tmp_path)

    real_import = builtins.__import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "faster_whisper" or name.startswith("faster_whisper."):
            raise ImportError("No module named 'faster_whisper'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(ImportError) as exc_info:
        transcribe(audio)

    msg = str(exc_info.value)
    assert "faster-whisper is required for voice pack ASR" in msg
    assert "pip install faster-whisper" in msg


def test_transcribe_empty_segment_list_returns_empty(tmp_path):
    audio = _make_audio(tmp_path)
    fake = _FakeModel([])

    out = transcribe(audio, model=fake)

    assert out == []
