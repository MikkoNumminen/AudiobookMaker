"""Unit tests for :mod:`src.voice_pack.diarize_ecapa`.

These tests never import speechbrain or torchaudio and never touch a real
audio file. A fake encoder and a fake audio loader are injected through
the ``encoder=`` and ``_load_audio_fn=`` kwargs.
"""

from __future__ import annotations

import pytest

# torch is only installed in the heavy .venv-chatterbox environment. The
# pre-commit hook runs tests in a lighter Python, so skip the whole module
# gracefully there rather than crashing at import time.
torch = pytest.importorskip("torch")
np = pytest.importorskip("numpy")

from src.voice_pack.diarize_ecapa import (  # noqa: E402
    _cluster_embeddings,
    _resolve_device,
    _slice_and_filter_windows,
    _windows_to_turns,
    diarize_ecapa,
)


class _FakeEncoder:
    """Returns pre-baked embeddings keyed by the window order they arrive.

    Mimics :meth:`speechbrain.inference.speaker.EncoderClassifier.encode_batch`
    which returns a ``[B, 1, D]`` tensor.
    """

    def __init__(self, embeddings: np.ndarray) -> None:
        self._emb = embeddings
        self._cursor = 0

    def encode_batch(self, batch: torch.Tensor) -> torch.Tensor:
        n = batch.shape[0]
        out = self._emb[self._cursor : self._cursor + n]
        self._cursor += n
        return torch.from_numpy(out[:, None, :]).float()


def test_slice_and_filter_windows_skips_silence():
    sr = 16000
    audio = torch.zeros(sr * 3)
    audio[sr : sr * 2] = 0.1  # 1 s of voiced audio in the middle
    windows, starts = _slice_and_filter_windows(
        audio,
        sr,
        window_s=1.0,
        hop_s=0.5,
        rms_silent_threshold=0.005,
    )
    assert len(windows) == len(starts)
    # All surviving windows must overlap the voiced region.
    assert len(windows) >= 1
    for s, w in zip(starts, windows):
        assert s + 1.0 > 1.0  # window end past 1.0s (into voiced region)
        assert w.shape == (sr,)


def test_slice_and_filter_windows_all_silence_drops_everything():
    sr = 16000
    audio = torch.zeros(sr * 2)
    windows, starts = _slice_and_filter_windows(
        audio,
        sr,
        window_s=1.0,
        hop_s=0.5,
        rms_silent_threshold=0.005,
    )
    assert windows == []
    assert starts == []


def test_cluster_embeddings_with_num_speakers():
    X = np.array(
        [
            [1.0, 0.0],
            [0.99, 0.01],
            [0.0, 1.0],
            [0.01, 0.99],
        ],
        dtype=np.float32,
    )
    X = X / np.linalg.norm(X, axis=1, keepdims=True)
    labels = _cluster_embeddings(X, num_speakers=2, distance_threshold=0.25)
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]


def test_cluster_embeddings_distance_threshold_auto_detects():
    X = np.array(
        [
            [1.0, 0.0],
            [0.99, 0.01],
            [0.0, 1.0],
        ],
        dtype=np.float32,
    )
    X = X / np.linalg.norm(X, axis=1, keepdims=True)
    labels = _cluster_embeddings(X, num_speakers=None, distance_threshold=0.25)
    assert labels[0] == labels[1]
    assert labels[0] != labels[2]


def test_cluster_embeddings_empty_input():
    X = np.zeros((0, 192), dtype=np.float32)
    labels = _cluster_embeddings(X, num_speakers=2, distance_threshold=0.25)
    assert labels.shape == (0,)


def test_cluster_embeddings_fewer_samples_than_clusters_collapses():
    X = np.array([[1.0, 0.0]], dtype=np.float32)
    X = X / np.linalg.norm(X, axis=1, keepdims=True)
    labels = _cluster_embeddings(X, num_speakers=2, distance_threshold=0.25)
    # Degenerate case: one window can't be two clusters. Must not crash.
    assert labels.tolist() == [0]


def test_windows_to_turns_merges_adjacent_same_speaker():
    # Three overlapping windows in cluster 0 → one turn. Then cluster 1.
    starts = [0.0, 0.75, 1.5, 3.5]
    labels = np.array([0, 0, 0, 1])
    turns = _windows_to_turns(starts, labels, window_s=1.5, merge_gap_s=0.5)
    assert len(turns) == 2
    assert turns[0].speaker == "SPEAKER_00"
    assert turns[0].start == 0.0
    assert turns[0].end == 3.0
    assert turns[1].speaker == "SPEAKER_01"
    assert turns[1].start == 3.5


def test_windows_to_turns_wide_gap_produces_separate_turns():
    # Same speaker but a large gap → two turns, not merged.
    starts = [0.0, 10.0]
    labels = np.array([0, 0])
    turns = _windows_to_turns(starts, labels, window_s=1.5, merge_gap_s=0.5)
    assert len(turns) == 2
    assert all(t.speaker == "SPEAKER_00" for t in turns)


def test_diarize_ecapa_end_to_end_with_fakes(tmp_path):
    audio_path = tmp_path / "a.wav"
    audio_path.write_bytes(b"x")

    sr = 16000
    audio = torch.full((sr * 4,), 0.1)  # 4 s of voiced audio

    def fake_loader(_path):
        return audio, sr

    # With window=1.5s hop=0.75s on 4s audio, the non-silent windows
    # start at 0.00, 0.75, 1.50, 2.25 (4 windows). Give first two an
    # embedding in cluster A, last two in cluster B.
    embeddings = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.99, 0.01, 0.0],
            [0.0, 1.0, 0.0],
            [0.01, 0.99, 0.0],
        ],
        dtype=np.float32,
    )
    embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
    encoder = _FakeEncoder(embeddings)

    turns = diarize_ecapa(
        audio_path,
        encoder=encoder,
        num_speakers=2,
        _load_audio_fn=fake_loader,
    )
    speakers = {t.speaker for t in turns}
    assert speakers == {"SPEAKER_00", "SPEAKER_01"}
    # Each speaker's turns span a contiguous region.
    for t in turns:
        assert t.end > t.start


def test_diarize_ecapa_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="Audio file not found"):
        diarize_ecapa(tmp_path / "nope.wav", encoder=object())


def test_diarize_ecapa_empty_audio_returns_no_turns(tmp_path):
    audio_path = tmp_path / "a.wav"
    audio_path.write_bytes(b"x")

    def fake_loader(_path):
        return torch.zeros(0), 16000

    turns = diarize_ecapa(
        audio_path,
        encoder=object(),  # never called
        num_speakers=2,
        _load_audio_fn=fake_loader,
    )
    assert turns == []


def test_diarize_ecapa_all_silence_returns_no_turns(tmp_path):
    audio_path = tmp_path / "a.wav"
    audio_path.write_bytes(b"x")
    audio = torch.zeros(16000 * 3)

    def fake_loader(_path):
        return audio, 16000

    turns = diarize_ecapa(
        audio_path,
        encoder=object(),
        num_speakers=2,
        _load_audio_fn=fake_loader,
    )
    assert turns == []


def test_diarize_ecapa_accepts_hf_token_kwarg_for_compat(tmp_path):
    """The hf_token kwarg is ignored but must be accepted so that the CLI
    can pass the same kwarg dict to either backend without branching."""
    audio_path = tmp_path / "a.wav"
    audio_path.write_bytes(b"x")

    def fake_loader(_path):
        return torch.zeros(0), 16000

    turns = diarize_ecapa(
        audio_path,
        hf_token="ignored",
        encoder=object(),
        _load_audio_fn=fake_loader,
    )
    assert turns == []


def test_resolve_device_normalises_bare_cuda_to_indexed():
    """speechbrain's EncoderClassifier splits the device string on ``:``
    and emits a stderr warning ("Could not parse CUDA device string
    'cuda': not enough values to unpack ... Falling back to device 0.")
    when there is no index. Always hand it ``cuda:0`` instead so the
    warning never fires. See interfaces.py in speechbrain.
    """
    assert _resolve_device("cuda") == "cuda:0"


def test_resolve_device_passes_through_cpu_and_indexed_cuda():
    assert _resolve_device("cpu") == "cpu"
    assert _resolve_device("cuda:1") == "cuda:1"
    assert _resolve_device("cuda:0") == "cuda:0"


def test_resolve_device_auto_without_cuda_is_cpu(monkeypatch):
    """Without a visible GPU, auto resolves to cpu and doesn't crash."""
    import torch as _torch

    monkeypatch.setattr(_torch.cuda, "is_available", lambda: False)
    assert _resolve_device("auto") == "cpu"


def test_resolve_device_auto_with_cuda_is_indexed(monkeypatch):
    """When auto picks GPU, it must pick ``cuda:0`` not bare ``cuda``."""
    import torch as _torch

    monkeypatch.setattr(_torch.cuda, "is_available", lambda: True)
    assert _resolve_device("auto") == "cuda:0"
