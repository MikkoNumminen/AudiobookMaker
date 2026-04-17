"""ASR stage for the voice pack pipeline.

This module is a thin wrapper around `faster-whisper`_ (the CTranslate2
Whisper implementation — faster and lighter on memory than OpenAI's
reference ``openai-whisper``, MIT licensed). It takes one audio file and
returns a list of :class:`~src.voice_pack.types.AsrSegment` records that
later stages (diarization, bucketing) intersect against speaker turns.

``faster-whisper`` is an *optional* dependency: it is not in
``requirements.txt`` because only voice-pack power users need it. The
import is therefore deferred to call time, and failure to import is
surfaced as a clear :class:`ImportError` telling the user exactly which
pip package to install. Tests inject a fake model via the ``model``
kwarg on :func:`transcribe`, so the test suite never needs the real
package installed.

.. _faster-whisper: https://github.com/SYSTRAN/faster-whisper
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.voice_pack.types import AsrSegment


_FASTER_WHISPER_MISSING_MSG = (
    "faster-whisper is required for voice pack ASR. "
    "Install with: pip install faster-whisper"
)


def _normalize_logprob(avg_logprob: float | None) -> float:
    """Normalise a whisper ``avg_logprob`` into ``[0.0, 1.0]``.

    ``avg_logprob`` is the mean token log-probability the decoder reports
    for a segment. It is typically in ``[-1.0, 0.0]`` for confident
    transcriptions and more negative for uncertain ones. We clamp
    ``1.0 + avg_logprob`` into ``[0.0, 1.0]`` so downstream code can
    treat confidence as a plain probability-like score.

    Args:
        avg_logprob: Whisper's mean token log-probability, or ``None``
            if the backend did not report one.

    Returns:
        A float in ``[0.0, 1.0]``. ``None`` maps to ``1.0`` (i.e. treat
        "no confidence reported" as "trust it" — the same convention
        used elsewhere in the pipeline, see :class:`AsrSegment`).
    """
    if avg_logprob is None:
        return 1.0
    return max(0.0, min(1.0, 1.0 + float(avg_logprob)))


def _resolve_device(device: str) -> str:
    """Resolve the ``"auto"`` device sentinel to a concrete device string.

    Args:
        device: ``"auto"``, ``"cpu"``, or ``"cuda"``.

    Returns:
        ``"cuda"`` if ``device == "auto"`` and a CUDA-capable torch is
        importable and reports ``cuda.is_available()``, else ``"cpu"``.
        Non-auto values are returned unchanged.
    """
    if device != "auto":
        return device
    try:
        import torch  # type: ignore[import-not-found]
    except Exception:
        return "cpu"
    try:
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        return "cpu"
    return "cpu"


def _resolve_compute_type(compute_type: str, device: str) -> str:
    """Resolve the ``"auto"`` compute-type sentinel.

    Args:
        compute_type: ``"auto"``, ``"float16"``, ``"int8_float16"``, or
            ``"int8"``.
        device: The already-resolved device (``"cuda"`` or ``"cpu"``).

    Returns:
        ``"float16"`` on CUDA and ``"int8"`` on CPU when
        ``compute_type == "auto"``; otherwise the value is returned
        unchanged.
    """
    if compute_type != "auto":
        return compute_type
    return "float16" if device == "cuda" else "int8"


def load_model(
    model_size: str = "large-v3",
    device: str = "auto",
    compute_type: str = "auto",
) -> Any:
    """Load a ``faster_whisper.WhisperModel``.

    Exposed separately from :func:`transcribe` so a long-running caller
    (e.g. the voice-pack CLI processing a directory of files) can load
    the model once and reuse it across many audio files.

    Args:
        model_size: Whisper model size, e.g. ``"tiny"``, ``"base"``,
            ``"small"``, ``"medium"``, ``"large-v2"``, ``"large-v3"``.
        device: ``"auto"`` (pick CUDA if available), ``"cpu"``, or
            ``"cuda"``.
        compute_type: ``"auto"`` (``float16`` on CUDA, ``int8`` on CPU),
            ``"float16"``, ``"int8_float16"``, or ``"int8"``.

    Returns:
        A ready-to-use ``faster_whisper.WhisperModel`` instance.

    Raises:
        ImportError: If ``faster-whisper`` is not installed.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise ImportError(_FASTER_WHISPER_MISSING_MSG) from exc

    resolved_device = _resolve_device(device)
    resolved_compute = _resolve_compute_type(compute_type, resolved_device)
    return WhisperModel(model_size, device=resolved_device, compute_type=resolved_compute)


def transcribe(
    audio_path: str | Path,
    *,
    model_size: str = "large-v3",
    device: str = "auto",
    compute_type: str = "auto",
    language: str | None = None,
    vad_filter: bool = True,
    beam_size: int = 5,
    model: object | None = None,
) -> list[AsrSegment]:
    """Transcribe ``audio_path`` with faster-whisper.

    Args:
        audio_path: Path to the audio file. Must exist on disk before
            the call — we check up front so we fail with a clean
            :class:`FileNotFoundError` rather than whatever error the
            backend would raise.
        model_size: Whisper model size (see :func:`load_model`). Ignored
            when ``model`` is provided.
        device: ``"auto"``, ``"cpu"``, or ``"cuda"``. Ignored when
            ``model`` is provided.
        compute_type: ``"auto"``, ``"float16"``, ``"int8_float16"``, or
            ``"int8"``. Ignored when ``model`` is provided.
        language: ISO language code (e.g. ``"en"``, ``"fi"``) to force
            the decoder language, or ``None`` to let whisper auto-detect.
        vad_filter: Enable whisper's built-in voice-activity filter to
            skip silent regions. Usually a win for long audiobooks.
        beam_size: Decoder beam size. Larger = slower but typically more
            accurate.
        model: Optional preloaded ``faster_whisper.WhisperModel`` (or a
            fake with the same ``.transcribe()`` shape, used by tests).
            When supplied, ``model_size`` / ``device`` / ``compute_type``
            are ignored and ``faster-whisper`` does not need to be
            installed.

    Returns:
        A list of :class:`AsrSegment` records, in start-time order, with
        empty-text segments dropped and whitespace stripped.

    Raises:
        FileNotFoundError: If ``audio_path`` does not exist.
        ImportError: If ``model`` is not supplied and ``faster-whisper``
            is not installed.
    """
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    whisper_model = model if model is not None else load_model(
        model_size=model_size, device=device, compute_type=compute_type
    )

    segments_iter, _info = whisper_model.transcribe(
        str(path),
        language=language,
        vad_filter=vad_filter,
        beam_size=beam_size,
    )

    results: list[AsrSegment] = []
    for seg in segments_iter:
        text = (seg.text or "").strip()
        if not text:
            continue
        results.append(
            AsrSegment(
                start=float(seg.start),
                end=float(seg.end),
                text=text,
                confidence=_normalize_logprob(getattr(seg, "avg_logprob", None)),
            )
        )
    return results
