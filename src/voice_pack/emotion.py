"""Emotion tagging for voice pack chunks.

Wraps the SpeechBrain ``speechbrain/emotion-recognition-wav2vec2-IEMOCAP``
classifier (MIT-licensed) to attach an emotion label to every
:class:`~src.voice_pack.types.VoiceChunk` produced upstream.

The IEMOCAP classifier emits four short labels (``ang``/``hap``/``sad``/
``neu``); this module normalises them to the canonical names declared in
:data:`src.voice_pack.types.EMOTION_CLASSES` (``angry``/``happy``/``sad``/
``neutral``). Anything else becomes ``"unknown"`` with confidence ``0.0``.

The audio loader and the classifier are both injectable so tests can
exercise the pipeline without pulling in speechbrain, pydub, or torch
weights.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from src.voice_pack.types import TaggedChunk, VoiceChunk

# SpeechBrain's IEMOCAP classifier uses 16 kHz mono input.
_TARGET_SR: int = 16000

# Minimum usable slice length (in samples at the target rate). Anything
# shorter than 100 ms carries too little signal for the classifier; we skip
# it and mark the chunk as ``unknown``.
_MIN_SLICE_SAMPLES: int = int(_TARGET_SR * 0.1)

# IEMOCAP short-label → canonical EMOTION_CLASSES name.
_LABEL_MAP: dict[str, str] = {
    "ang": "angry",
    "hap": "happy",
    "sad": "sad",
    "neu": "neutral",
}

AudioLoader = Callable[[Any], Tuple[Any, int]]


def _resolve_device(device: str) -> str:
    """Pick a torch device string, honouring ``"auto"``.

    Args:
        device: Either ``"auto"``, ``"cpu"``, or ``"cuda"``.

    Returns:
        A concrete device string (``"cpu"`` or ``"cuda"``).
    """
    if device != "auto":
        return device
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def load_classifier(device: str = "auto") -> object:
    """Load the SpeechBrain emotion classifier.

    Kept separate from :func:`tag_emotions` so callers can cache the
    classifier across multiple audiobook files — it is the expensive
    initialisation step (model download + weight load).

    Args:
        device: ``"auto"``, ``"cpu"``, or ``"cuda"``.

    Returns:
        A SpeechBrain ``foreign_class`` instance exposing
        ``classify_batch(tensor)``.

    Raises:
        ImportError: If speechbrain is not installed.
    """
    try:
        from speechbrain.inference.interfaces import foreign_class  # type: ignore
    except Exception as exc:  # pragma: no cover - exercised via ImportError path
        raise ImportError(
            "speechbrain is required for voice pack emotion tagging. "
            "Install with: pip install speechbrain"
        ) from exc

    resolved = _resolve_device(device)
    return foreign_class(
        source="speechbrain/emotion-recognition-wav2vec2-IEMOCAP",
        pymodule_file="custom_interface.py",
        classname="CustomEncoderWav2vec2Classifier",
        run_opts={"device": resolved},
    )


def _default_audio_loader(path: Any) -> Tuple[Any, int]:
    """Load audio via pydub and return ``(float32 mono samples, sr)``.

    Converts int16 PCM to float32 in ``[-1.0, 1.0]``. Stereo is reduced to
    mono by averaging channels.

    Args:
        path: Filesystem path to the audio file.

    Returns:
        A ``(numpy.ndarray, sample_rate)`` pair. The array is 1-D float32.
    """
    import numpy as np  # type: ignore
    from pydub import AudioSegment  # type: ignore

    seg = AudioSegment.from_file(str(path))
    sr = seg.frame_rate
    channels = seg.channels
    raw = np.array(seg.get_array_of_samples(), dtype=np.int16)
    if channels > 1:
        raw = raw.reshape((-1, channels))
    samples = raw.astype(np.float32) / 32768.0
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    return samples, sr


def _resample_linear(samples: Any, src_sr: int, tgt_sr: int) -> Any:
    """Resample a 1-D waveform with linear interpolation.

    Dependency-free (numpy only) so we don't pull scipy into the voice
    pack pipeline.

    Args:
        samples: 1-D float32 waveform.
        src_sr: Source sample rate in Hz.
        tgt_sr: Target sample rate in Hz.

    Returns:
        The resampled 1-D float32 waveform. If ``src_sr == tgt_sr`` the
        input array is returned unchanged.
    """
    import numpy as np  # type: ignore

    if src_sr == tgt_sr:
        return samples
    n_src = int(samples.shape[0])
    if n_src == 0:
        return samples
    n_tgt = max(1, int(round(n_src * tgt_sr / src_sr)))
    # Map every target index back onto the source timeline.
    src_indices = np.linspace(0.0, n_src - 1, num=n_tgt, dtype=np.float64)
    left = np.floor(src_indices).astype(np.int64)
    right = np.minimum(left + 1, n_src - 1)
    frac = (src_indices - left).astype(np.float32)
    out = samples[left] * (1.0 - frac) + samples[right] * frac
    return out.astype(np.float32)


def _to_mono(samples: Any) -> Any:
    """Collapse a possibly-stereo waveform to mono by averaging channels.

    Args:
        samples: 1-D or 2-D numpy array.

    Returns:
        A 1-D float32 numpy array.
    """
    if getattr(samples, "ndim", 1) == 2:
        return samples.mean(axis=1).astype("float32")
    return samples


def _classify_slice(model: object, slice_samples: Any) -> Tuple[str, float]:
    """Run one audio slice through the classifier and normalise the label.

    Args:
        model: A SpeechBrain classifier exposing ``classify_batch``.
        slice_samples: 1-D float32 mono waveform at 16 kHz.

    Returns:
        ``(canonical_label, confidence)``. Unknown labels map to
        ``("unknown", 0.0)``.
    """
    import torch  # type: ignore

    batch = torch.tensor(slice_samples).unsqueeze(0)
    out_prob, score, index, text_lab = model.classify_batch(batch)  # type: ignore[attr-defined]
    raw = str(text_lab[0]).lower().strip()
    canonical = _LABEL_MAP.get(raw, "unknown")

    try:
        confidence = float(score[0].item() if hasattr(score[0], "item") else score[0])
    except Exception:
        try:
            confidence = float(score)
        except Exception:
            confidence = 0.0

    if canonical == "unknown":
        # We can't trust the confidence of a label we don't recognise, and
        # downstream rebalancing treats unknown as "skip" anyway.
        return "unknown", 0.0
    return canonical, confidence


def tag_emotions(
    chunks: list[VoiceChunk],
    audio_path: str | Path,
    *,
    device: str = "auto",
    model: Optional[object] = None,
    audio_loader: Optional[AudioLoader] = None,
) -> list[TaggedChunk]:
    """Classify the emotion of each :class:`VoiceChunk`.

    For every chunk, slices the source audio to ``[start, end]``, resamples
    to 16 kHz mono if needed, runs the slice through the SpeechBrain
    classifier, and returns a :class:`TaggedChunk` with the predicted label
    and confidence attached.

    Chunks that fail classification — too short, classifier error, unknown
    label — are returned with ``emotion="unknown"`` and
    ``emotion_confidence=0.0``. Input order is preserved.

    Args:
        chunks: The voice chunks to tag. An empty list returns ``[]``
            without reading the audio file.
        audio_path: Path to the source audio file.
        device: ``"auto"``, ``"cpu"``, or ``"cuda"`` — forwarded to
            :func:`load_classifier` when ``model`` is not supplied.
        model: Optional preloaded classifier. Callers that tag many files
            should load once and reuse.
        audio_loader: Optional override for the audio loader. Must return
            ``(samples, sample_rate)`` where ``samples`` is a numpy array
            (1-D mono or 2-D ``[N, channels]``). Defaults to a pydub-based
            loader.

    Returns:
        A list of :class:`TaggedChunk`, same length and order as ``chunks``.

    Raises:
        FileNotFoundError: If ``audio_path`` does not exist.
        ImportError: If speechbrain is needed (no ``model`` injected) and
            is not installed.
    """
    if not chunks:
        return []

    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    loader = audio_loader if audio_loader is not None else _default_audio_loader
    samples, sr = loader(path)
    samples = _to_mono(samples)

    if sr != _TARGET_SR:
        samples = _resample_linear(samples, sr, _TARGET_SR)
        sr = _TARGET_SR

    classifier = model if model is not None else load_classifier(device=device)

    tagged: list[TaggedChunk] = []
    total = int(getattr(samples, "shape", (0,))[0])

    for chunk in chunks:
        start_idx = max(0, int(chunk.start * sr))
        end_idx = min(total, int(chunk.end * sr))
        slice_len = end_idx - start_idx

        if slice_len <= 0 or slice_len < _MIN_SLICE_SAMPLES:
            tagged.append(TaggedChunk.from_chunk(chunk))
            continue

        slice_samples = samples[start_idx:end_idx]

        try:
            label, confidence = _classify_slice(classifier, slice_samples)
        except Exception:
            tagged.append(TaggedChunk.from_chunk(chunk))
            continue

        tagged.append(
            TaggedChunk.from_chunk(
                chunk, emotion=label, emotion_confidence=confidence
            )
        )

    return tagged
