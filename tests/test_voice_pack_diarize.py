"""Unit tests for :mod:`src.voice_pack.diarize`.

These tests never import pyannote, never touch a real HF token, and never
require a GPU. A fake pipeline is injected through the ``pipeline=`` kwarg
(for diarize tests) or via sys.modules patching of ``pyannote.audio``
(for load_pipeline tests).
"""

from __future__ import annotations

import logging
import sys
import types

import pytest

from src.voice_pack.diarize import _merge_adjacent, diarize, load_pipeline, resolve_token
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


def test_hf_token_shim_runs_body_exactly_once_under_concurrent_callers(
    monkeypatch,
):
    """Concurrent first-callers must not both run the patch body.

    Without the module-level lock the check-then-set pattern raced:
    thread A read `_HF_TOKEN_SHIM_APPLIED == False`, thread B read
    the same, both entered the body, both replaced `hf_hub_download`
    with a wrapper closing over the *previous* binding — second
    wrapper called first wrapper, every real call paid two argument-
    translation layers. The lock + double-check inside guarantees
    the body runs exactly once.
    """
    import threading
    from src.voice_pack import diarize as diarize_mod

    # Fake huggingface_hub so the shim can patch something.
    fake_hub = types.ModuleType("huggingface_hub")
    real_download = lambda *a, **k: None  # noqa: E731
    fake_hub.hf_hub_download = real_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)

    # Reset idempotency flag so the shim actually runs.
    monkeypatch.setattr(diarize_mod, "_HF_TOKEN_SHIM_APPLIED", False)

    start = threading.Barrier(8)

    def runner() -> None:
        start.wait()
        diarize_mod._apply_hf_token_shim()

    threads = [threading.Thread(target=runner) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # If the body ran twice, the patched download would be a wrapper-
    # around-a-wrapper. The shim closes over `original`, which was
    # captured BEFORE the assignment. After exactly one patch,
    # `fake_hub.hf_hub_download` is the wrapper closing over
    # `real_download`; after two patches it would close over the
    # first wrapper. Inspect the closure to verify only one layer.
    patched = fake_hub.hf_hub_download
    assert patched is not real_download, "shim must have patched once"
    closure_vars = {
        cell.cell_contents
        for cell in (patched.__closure__ or ())
    }
    assert real_download in closure_vars, (
        "shim wrapped the real function exactly once; if double-wrapped, "
        "the closure would point at the first wrapper, not the real fn"
    )


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


# ---------------------------------------------------------------------------
# load_pipeline: gated-repo error surfacing tests
# ---------------------------------------------------------------------------

def _make_fake_pyannote_audio(exc_to_raise: Exception) -> types.ModuleType:
    """Build a minimal fake ``pyannote.audio`` module whose ``Pipeline``
    raises *exc_to_raise* when ``from_pretrained`` is called."""
    class _FailPipeline:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            raise exc_to_raise

    fake_audio = types.ModuleType("pyannote.audio")
    fake_audio.Pipeline = _FailPipeline  # type: ignore[attr-defined]
    return fake_audio


def _install_fake_pyannote(monkeypatch, exc: Exception) -> None:
    """Inject a fake pyannote.audio (and its parent) into sys.modules.

    Also skips the HF-token and torch shims (they need real optional deps)
    by marking them as already applied, and supplies a stub huggingface_hub
    so the shim import guard doesn't fail when the flag is reset.
    """
    from src.voice_pack import diarize as diarize_mod

    fake_audio = _make_fake_pyannote_audio(exc)
    # The parent package must also be present so Python's import machinery
    # doesn't complain when ``from pyannote.audio import Pipeline`` runs.
    fake_pyannote = types.ModuleType("pyannote")
    fake_pyannote.audio = fake_audio  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyannote", fake_pyannote)
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake_audio)

    # Mark the optional-dependency shims as already applied so they are
    # no-ops during the test. These shims import huggingface_hub / torch /
    # speechbrain which are not installed in the unit-test environment.
    monkeypatch.setattr(diarize_mod, "_HF_TOKEN_SHIM_APPLIED", True)
    monkeypatch.setattr(diarize_mod, "_TORCH_LOAD_SHIM_APPLIED", True)
    monkeypatch.setattr(diarize_mod, "_SPEECHBRAIN_LAZY_SHIM_APPLIED", True)


def test_load_pipeline_403_raises_clear_runtime_error(monkeypatch):
    """A 403 Forbidden from HF must surface as a clear RuntimeError with
    the license-accept URL, not a raw traceback."""
    original_exc = Exception("403 Forbidden: Access denied to gated model")
    _install_fake_pyannote(monkeypatch, original_exc)
    monkeypatch.setenv("HF_TOKEN", "fake-token")

    with pytest.raises(RuntimeError) as exc_info:
        load_pipeline()

    msg = str(exc_info.value)
    assert "pyannote/speaker-diarization-3.1" in msg
    assert "Agree and access repository" in msg
    # Original exception must be chained
    assert exc_info.value.__cause__ is original_exc


def test_load_pipeline_gated_repo_error_raises_clear_runtime_error(monkeypatch):
    """A GatedRepoError-style message must also produce the friendly error."""
    original_exc = Exception("GatedRepoError: This repository is gated")
    _install_fake_pyannote(monkeypatch, original_exc)
    monkeypatch.setenv("HF_TOKEN", "fake-token")

    with pytest.raises(RuntimeError) as exc_info:
        load_pipeline()

    msg = str(exc_info.value)
    assert "pyannote/speaker-diarization-3.1" in msg
    assert "Agree and access repository" in msg
    assert exc_info.value.__cause__ is original_exc


def test_load_pipeline_401_raises_clear_runtime_error(monkeypatch):
    """A 401 Unauthorized must also trigger the friendly error."""
    original_exc = Exception("401 Unauthorized")
    _install_fake_pyannote(monkeypatch, original_exc)
    monkeypatch.setenv("HF_TOKEN", "fake-token")

    with pytest.raises(RuntimeError) as exc_info:
        load_pipeline()

    msg = str(exc_info.value)
    assert "pyannote/speaker-diarization-3.1" in msg
    assert "Agree and access repository" in msg
    assert exc_info.value.__cause__ is original_exc


def test_load_pipeline_gated_lowercase_raises_clear_runtime_error(monkeypatch):
    """The keyword match must be case-insensitive (e.g. 'gated repo')."""
    original_exc = Exception("access to this gated repo is restricted")
    _install_fake_pyannote(monkeypatch, original_exc)
    monkeypatch.setenv("HF_TOKEN", "fake-token")

    with pytest.raises(RuntimeError) as exc_info:
        load_pipeline()

    msg = str(exc_info.value)
    assert "pyannote/speaker-diarization-3.1" in msg
    assert exc_info.value.__cause__ is original_exc


def test_load_pipeline_network_error_passes_through_unchanged(monkeypatch):
    """A generic network error must NOT be wrapped — it should propagate as-is
    so real failures aren't hidden behind the license-accept message."""
    original_exc = RuntimeError("network down: connection refused")
    _install_fake_pyannote(monkeypatch, original_exc)
    monkeypatch.setenv("HF_TOKEN", "fake-token")

    with pytest.raises(RuntimeError, match="network down") as exc_info:
        load_pipeline()

    # Must be the original exception, not a re-wrapped one
    assert exc_info.value is original_exc


def test_load_pipeline_error_message_contains_url(monkeypatch):
    """The friendly error must include the full model URL so the user can
    navigate there directly."""
    _install_fake_pyannote(monkeypatch, Exception("403 Forbidden"))
    monkeypatch.setenv("HF_TOKEN", "fake-token")

    with pytest.raises(RuntimeError) as exc_info:
        load_pipeline()

    assert "https://huggingface.co/pyannote/speaker-diarization-3.1" in str(exc_info.value)
