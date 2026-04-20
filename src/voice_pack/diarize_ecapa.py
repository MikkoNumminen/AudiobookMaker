"""Pyannote-free speaker diarization via ECAPA-TDNN + agglomerative clustering.

Alternative to :mod:`src.voice_pack.diarize` for sources where the pyannote
HF-gated ``speaker-diarization-3.1`` model isn't available (no HF account,
model license not accepted) or where it has misbehaved on the source. The
ECAPA weights at ``speechbrain/spkrec-ecapa-voxceleb`` are not gated, so
this module works without a Hugging Face token.

Approach:

1. Resample audio to 16 kHz mono.
2. Slice into overlapping windows (default 1.5 s window, 0.75 s hop).
3. Drop windows below an RMS silence floor.
4. Embed each window with ECAPA-TDNN (192-dim output).
5. L2-normalise, then agglomerative cosine clustering. Either an explicit
   ``num_speakers`` (operator knows the cast size) or a
   ``distance_threshold`` (auto-detect).
6. Merge adjacent same-cluster windows into :class:`DiarTurn` objects.

The prototype at ``d:/tmp/analyze_ecapa.py`` ran successfully on the Dual
Class 1h sample — this module is the productised version. Key additions
over the prototype:

* ``local_strategy=LocalStrategy.COPY`` on encoder load. Required on
  Windows where symlink creation without admin rights fails with
  ``OSError: [WinError 1314]``.
* ``distance_threshold`` mode for when the operator doesn't know
  ``num_speakers`` in advance.
* Injectable ``encoder`` and ``_load_audio_fn`` so the test suite can
  exercise the pipeline without loading speechbrain or torchaudio.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.voice_pack.types import DiarTurn

# Defaults tuned on the Dual Class 1h sample. Operators with very
# different sources (rapid dialogue, short turns) may want to override.
_DEFAULT_WINDOW_S = 1.5
_DEFAULT_HOP_S = 0.75
_DEFAULT_RMS_SILENT = 0.005
_DEFAULT_DISTANCE_THRESHOLD = 0.25
# Wider than the pyannote path's 0.1s because ECAPA windows are coarser
# (1.5s) and adjacent same-speaker windows often have a half-second gap.
_DEFAULT_MERGE_GAP_S = 0.5
_ECAPA_SR = 16000
_ECAPA_EMBED_DIM = 192


def _resolve_device(device: str) -> str:
    """Turn ``"auto"`` into a concrete ``"cpu"`` / ``"cuda:N"`` selection.

    speechbrain's ``EncoderClassifier`` parses the device string with
    ``device.split(":")`` and logs a noisy warning to stderr when the
    split doesn't yield ``(type, index)`` — e.g. when the caller passes a
    bare ``"cuda"``. We normalise GPU strings to always include an index
    so that warning never fires. ``"cpu"`` and any already-indexed string
    like ``"cuda:1"`` are passed through unchanged.
    """
    if device == "auto":
        try:
            import torch  # type: ignore[import-not-found]

            if torch.cuda.is_available():
                return "cuda:0"
        except Exception:  # pragma: no cover - torch missing or broken
            pass
        return "cpu"
    if device == "cuda":
        return "cuda:0"
    return device


def load_encoder(
    device: str = "cpu", cache_dir: str | Path | None = None
) -> Any:
    """Load the ECAPA-TDNN speaker encoder.

    Kept separate so callers processing multiple files can reuse the
    model. Always passes ``local_strategy=LocalStrategy.COPY`` — required
    on Windows where symlink creation without admin rights fails.

    Args:
        device: ``"cpu"`` (default), ``"cuda"``, or ``"auto"``. Default is
            ``"cpu"`` because ECAPA on GPU has produced a degenerate
            single-speaker clustering on a 1h dual-narrator source that
            CPU handles cleanly; see :func:`diarize_ecapa` for detail.
        cache_dir: Where to cache the ECAPA weights. Defaults to
            ``<repo>/.cache/ecapa``.
    """
    try:
        from speechbrain.inference.speaker import (  # type: ignore[import-not-found]
            EncoderClassifier,
        )
        from speechbrain.utils.fetching import (  # type: ignore[import-not-found]
            LocalStrategy,
        )
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(
            "speechbrain is required for the ECAPA diarizer. "
            "Install with: pip install speechbrain"
        ) from exc

    if cache_dir is None:
        repo_root = Path(__file__).resolve().parents[2]
        cache_dir = repo_root / ".cache" / "ecapa"

    chosen_device = _resolve_device(device)
    return EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=str(cache_dir),
        run_opts={"device": chosen_device},
        local_strategy=LocalStrategy.COPY,
    )


def _load_audio_mono_16k(audio_path: Path) -> tuple[Any, int]:
    """Load audio as a [samples] mono float tensor at 16 kHz."""
    import torchaudio  # type: ignore[import-not-found]

    waveform, sr = torchaudio.load(str(audio_path))
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    if sr != _ECAPA_SR:
        waveform = torchaudio.functional.resample(waveform, sr, _ECAPA_SR)
        sr = _ECAPA_SR
    return waveform.squeeze(0), sr


def _slice_and_filter_windows(
    audio: Any,
    sr: int,
    *,
    window_s: float,
    hop_s: float,
    rms_silent_threshold: float,
) -> tuple[list[Any], list[float]]:
    """Cut the audio into overlapping windows, drop silent ones.

    Returns two parallel lists: the voiced window tensors and their
    start times in seconds. Silent windows are dropped because their
    ECAPA embeddings carry almost no speaker information and would
    pollute the clustering.
    """
    import torch  # type: ignore[import-not-found]

    win_n = int(window_s * sr)
    hop_n = int(hop_s * sr)
    batch: list[Any] = []
    starts: list[float] = []
    pos = 0
    while pos + win_n <= audio.shape[0]:
        w = audio[pos : pos + win_n]
        rms = float(torch.sqrt(torch.mean(w * w)))
        if rms >= rms_silent_threshold:
            batch.append(w)
            starts.append(pos / sr)
        pos += hop_n
    return batch, starts


def _embed_windows(
    windows: list[Any],
    encoder: Any,
    *,
    batch_size: int = 64,
) -> Any:
    """Run ECAPA on the voiced windows, return L2-normalised embeddings.

    Returns an ``np.ndarray`` shaped ``[N, 192]``. The L2 normalisation
    makes cosine distance equivalent to (half the) Euclidean distance so
    agglomerative clustering behaves consistently.
    """
    import numpy as np
    import torch  # type: ignore[import-not-found]

    if not windows:
        return np.zeros((0, _ECAPA_EMBED_DIM), dtype=np.float32)

    embeds = []
    for i in range(0, len(windows), batch_size):
        chunk = torch.stack(windows[i : i + batch_size])
        with torch.no_grad():
            e = encoder.encode_batch(chunk)  # [b, 1, D]
        embeds.append(e.squeeze(1).cpu().numpy())
    X = np.concatenate(embeds, axis=0)
    X = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)
    return X


def _cluster_embeddings(
    X: Any,
    *,
    num_speakers: int | None,
    distance_threshold: float,
) -> Any:
    """Agglomerative cosine clustering over the window embeddings.

    When ``num_speakers`` is supplied it wins; otherwise the clustering
    cuts at ``distance_threshold``. Returns a per-window label array.
    """
    import numpy as np
    from sklearn.cluster import AgglomerativeClustering

    if X.shape[0] == 0:
        return np.zeros(0, dtype=int)

    if num_speakers is not None:
        if X.shape[0] < num_speakers:
            # Not enough windows to produce that many clusters — put
            # every window in cluster 0 so downstream code still works.
            return np.zeros(X.shape[0], dtype=int)
        model = AgglomerativeClustering(
            n_clusters=num_speakers,
            metric="cosine",
            linkage="average",
        )
    else:
        model = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=distance_threshold,
            metric="cosine",
            linkage="average",
        )
    return model.fit_predict(X)


def _windows_to_turns(
    starts: list[float],
    labels: Any,
    *,
    window_s: float,
    merge_gap_s: float,
) -> list[DiarTurn]:
    """Collapse per-window labels into merged :class:`DiarTurn` objects."""
    raw = sorted(
        (
            (float(starts[i]), float(starts[i]) + window_s, int(labels[i]))
            for i in range(len(starts))
        ),
        key=lambda t: t[0],
    )
    merged: list[tuple[float, float, int]] = []
    for s, e, lab in raw:
        if merged and merged[-1][2] == lab and s <= merged[-1][1] + merge_gap_s:
            ps, pe, pl = merged[-1]
            merged[-1] = (ps, max(pe, e), pl)
        else:
            merged.append((s, e, lab))
    return [
        DiarTurn(start=s, end=e, speaker=f"SPEAKER_{lab:02d}")
        for s, e, lab in merged
    ]


# Device default note: ECAPA diarization defaults to CPU. A verification
# run on a 1h dual-narrator source produced a degenerate split on GPU
# (one cluster ~51 min, the other ~0.4 min across 6 short chunks) while
# CPU on the same input gave a clean ~36 / ~13 minute split. Root cause
# of the GPU regression is not yet understood — candidates include cuDNN
# non-determinism in ECAPA embeddings, fp16 precision loss, or a device
# string mismatch somewhere in the embed path. Until that is resolved,
# default to CPU; the 1h PoC ran in ~167 s on CPU which is fast enough
# that this is not a throughput problem. Callers that want GPU can pass
# ``device="cuda"`` (or ``device="auto"``) explicitly. ASR stays on GPU.
def diarize_ecapa(
    audio_path: str | Path,
    *,
    hf_token: str | None = None,  # noqa: ARG001 — interface compat
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    device: str = "cpu",
    encoder: Any | None = None,
    cache_dir: str | Path | None = None,
    distance_threshold: float = _DEFAULT_DISTANCE_THRESHOLD,
    window_s: float = _DEFAULT_WINDOW_S,
    hop_s: float = _DEFAULT_HOP_S,
    rms_silent_threshold: float = _DEFAULT_RMS_SILENT,
    merge_gap_s: float = _DEFAULT_MERGE_GAP_S,
    verbose: bool = False,
    _load_audio_fn: Callable[[Path], tuple[Any, int]] | None = None,
) -> list[DiarTurn]:
    """Pyannote-free diarization. Drop-in replacement for :func:`diarize`.

    ``hf_token`` is accepted and ignored so the CLI's diarizer-selector
    can route kwargs uniformly to either backend.

    ``num_speakers`` wins when supplied. ``min_speakers`` /
    ``max_speakers`` are advisory only — agglomerative clustering has no
    clean way to enforce them — so they're noted in verbose output and
    ignored. Operators who know the exact cast should pass
    ``num_speakers``; otherwise tune ``distance_threshold``.

    ``device`` defaults to ``"cpu"`` because ECAPA on GPU has produced a
    degenerate clustering on a trusted dual-narrator source that CPU
    handles correctly. Pass ``device="cuda"`` or ``device="auto"``
    explicitly to re-enable GPU once the underlying issue is understood.
    """
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    if encoder is None:
        encoder = load_encoder(device=device, cache_dir=cache_dir)

    loader = _load_audio_fn or _load_audio_mono_16k
    audio, sr = loader(path)

    windows, starts = _slice_and_filter_windows(
        audio,
        sr,
        window_s=window_s,
        hop_s=hop_s,
        rms_silent_threshold=rms_silent_threshold,
    )
    if verbose:
        total_s = audio.shape[0] / sr if hasattr(audio, "shape") else 0.0
        print(
            f"[diarize_ecapa] {len(windows)} voiced windows, "
            f"audio={total_s:.1f}s",
            flush=True,
        )

    if not windows:
        return []

    X = _embed_windows(windows, encoder)

    if (
        verbose
        and num_speakers is None
        and (min_speakers is not None or max_speakers is not None)
    ):
        print(
            "[diarize_ecapa] min_speakers/max_speakers are advisory and "
            "ignored; pass --num-speakers for an exact count or tune "
            "distance_threshold to auto-detect.",
            flush=True,
        )

    labels = _cluster_embeddings(
        X,
        num_speakers=num_speakers,
        distance_threshold=distance_threshold,
    )

    if verbose:
        import numpy as np

        n_clusters = int(len(np.unique(labels)))
        print(
            f"[diarize_ecapa] clustered into {n_clusters} speakers",
            flush=True,
        )

    return _windows_to_turns(
        starts,
        labels,
        window_s=window_s,
        merge_gap_s=merge_gap_s,
    )
