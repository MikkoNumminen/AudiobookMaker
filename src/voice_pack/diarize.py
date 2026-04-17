"""Speaker diarization stage for the voice pack pipeline.

This module wraps :mod:`pyannote.audio` 3.x to answer the "who spoke when"
question for a single audio file. It is deliberately thin: load a pipeline,
run it, convert the result into our own :class:`DiarTurn` dataclass, merge
touching turns from the same speaker so downstream stages do not have to.

The pyannote 3.1 model is gated on Hugging Face. To use it you must:

1. Create a Hugging Face account.
2. Accept the model license at
   https://huggingface.co/pyannote/speaker-diarization-3.1
3. Generate a read token and expose it as ``HF_TOKEN`` in the environment
   (``HUGGINGFACE_TOKEN`` is also accepted as a fallback).

Tests can bypass all of this by injecting a fake pipeline via the
``pipeline=`` kwarg of :func:`diarize`; no token check is performed in that
path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.voice_pack.types import DiarTurn

if TYPE_CHECKING:  # pragma: no cover - import guard for type checkers only
    import pyannote.audio  # noqa: F401


# Default gap, in seconds, below which two same-speaker turns are merged.
_DEFAULT_MERGE_GAP_S = 0.1

# Pyannote model id. Centralised so it is easy to bump when a new revision
# ships.
_PYANNOTE_MODEL_ID = "pyannote/speaker-diarization-3.1"


def resolve_token(hf_token: str | None) -> str:
    """Return a Hugging Face token, or raise with a clear fix-it message.

    Args:
        hf_token: Explicit token. If non-empty, it wins.

    Returns:
        A non-empty token string.

    Raises:
        RuntimeError: If no token is available from args or environment.
    """
    if hf_token:
        return hf_token
    env_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if env_token:
        return env_token
    raise RuntimeError(
        "Hugging Face token required for pyannote diarization. "
        "Set env HF_TOKEN or pass hf_token=... "
        "Accept the model license at "
        "https://huggingface.co/pyannote/speaker-diarization-3.1"
    )


def _resolve_device(device: str) -> str:
    """Turn ``"auto"`` into a concrete ``"cpu"`` / ``"cuda"`` selection.

    Args:
        device: One of ``"auto"``, ``"cpu"``, ``"cuda"``.

    Returns:
        Either ``"cpu"`` or ``"cuda"``.
    """
    if device != "auto":
        return device
    try:
        import torch  # type: ignore[import-not-found]

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        # If torch is missing or misbehaves we silently fall back to CPU —
        # diarization is slow but still works, and the caller does not need
        # this to be fatal.
        pass
    return "cpu"


def load_pipeline(
    hf_token: str | None = None, device: str = "auto"
) -> "pyannote.audio.Pipeline":
    """Load the pyannote ``speaker-diarization-3.1`` pipeline.

    Kept separate from :func:`diarize` so callers that process many files can
    load the heavy model once and reuse it across calls.

    Args:
        hf_token: Hugging Face token. Falls back to env ``HF_TOKEN`` /
            ``HUGGINGFACE_TOKEN``.
        device: ``"auto"``, ``"cpu"``, or ``"cuda"``.

    Returns:
        A ready-to-call pyannote ``Pipeline`` instance.

    Raises:
        ImportError: If ``pyannote.audio`` is not installed.
        RuntimeError: If no HF token can be found.
    """
    token = resolve_token(hf_token)

    try:
        from pyannote.audio import Pipeline  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(
            "pyannote.audio is required for voice pack diarization. "
            "Install with: pip install pyannote.audio"
        ) from exc

    pipeline = Pipeline.from_pretrained(_PYANNOTE_MODEL_ID, use_auth_token=token)

    chosen_device = _resolve_device(device)
    if chosen_device == "cuda":
        try:
            import torch  # type: ignore[import-not-found]

            pipeline.to(torch.device("cuda"))
        except Exception:  # pragma: no cover - only triggers on broken cuda
            # If moving to cuda fails, stick with whatever device the
            # pipeline is already on rather than hard-crashing the caller.
            pass

    return pipeline


def _merge_adjacent(
    turns: list[DiarTurn], gap_seconds: float = _DEFAULT_MERGE_GAP_S
) -> list[DiarTurn]:
    """Fuse consecutive same-speaker turns separated by a tiny gap.

    Diarizers frequently emit two back-to-back turns for the same speaker
    with a sub-second gap between them — usually a breath, not a real
    handoff. Merging these once here means downstream bucketing code does
    not have to special-case it.

    Args:
        turns: Diarization turns, already sorted by ``start``.
        gap_seconds: Maximum gap to bridge. Defaults to 0.1 s.

    Returns:
        A new list with adjacent same-speaker turns merged.
    """
    if not turns:
        return []

    merged: list[DiarTurn] = [turns[0]]
    for current in turns[1:]:
        last = merged[-1]
        gap = current.start - last.end
        if current.speaker == last.speaker and gap < gap_seconds:
            merged[-1] = DiarTurn(
                start=last.start,
                end=max(last.end, current.end),
                speaker=last.speaker,
            )
        else:
            merged.append(current)
    return merged


def diarize(
    audio_path: str | Path,
    *,
    hf_token: str | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    num_speakers: int | None = None,
    device: str = "auto",
    pipeline: object | None = None,
) -> list[DiarTurn]:
    """Run speaker diarization on a single audio file.

    Args:
        audio_path: Path to the audio file to diarize.
        hf_token: Hugging Face token. Falls back to env ``HF_TOKEN`` /
            ``HUGGINGFACE_TOKEN``. Ignored if ``pipeline`` is supplied.
        min_speakers: Lower bound hint for the diarizer.
        max_speakers: Upper bound hint for the diarizer.
        num_speakers: Exact speaker count hint. Overrides min/max when given.
        device: ``"auto"``, ``"cpu"``, or ``"cuda"``. Ignored if
            ``pipeline`` is supplied.
        pipeline: Preloaded pyannote ``Pipeline`` (or a duck-typed fake for
            tests). When provided, skips the token/device setup path
            entirely.

    Returns:
        List of :class:`DiarTurn`, sorted by ``start`` ascending, with
        adjacent same-speaker turns merged.

    Raises:
        FileNotFoundError: If ``audio_path`` does not exist.
        ImportError: If ``pyannote.audio`` is required and not installed.
        RuntimeError: If no HF token is available.
    """
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    active_pipeline: Any = pipeline
    if active_pipeline is None:
        active_pipeline = load_pipeline(hf_token=hf_token, device=device)

    kwargs: dict[str, Any] = {}
    if num_speakers is not None:
        kwargs["num_speakers"] = num_speakers
    else:
        if min_speakers is not None:
            kwargs["min_speakers"] = min_speakers
        if max_speakers is not None:
            kwargs["max_speakers"] = max_speakers

    annotation = active_pipeline(str(path), **kwargs)

    turns: list[DiarTurn] = []
    for segment, _track_id, speaker_label in annotation.itertracks(yield_label=True):
        turns.append(
            DiarTurn(
                start=float(segment.start),
                end=float(segment.end),
                speaker=str(speaker_label),
            )
        )

    # Stable sort by start so ties preserve the diarizer's original order.
    turns.sort(key=lambda t: t.start)

    return _merge_adjacent(turns)
