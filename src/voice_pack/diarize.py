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

# Dev-time: pull HF_TOKEN from a repo-root .env if available. This keeps
# the module working when imported directly (e.g. from a REPL or a test
# script that didn't route through scripts/voice_pack_analyze.py, which
# already loads dotenv). override=False means an explicit shell export
# still wins. Frozen .exe builds skip this — dotenv isn't bundled and
# the install path doesn't use pyannote.
try:
    from dotenv import load_dotenv

    _repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(_repo_root / ".env", override=False)
except ImportError:
    pass

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


_HF_TOKEN_SHIM_APPLIED = False


def _apply_hf_token_shim() -> None:
    """Rename ``use_auth_token`` → ``token`` on ``hf_hub_download`` calls.

    pyannote.audio 3.x passes ``use_auth_token`` straight through to
    ``huggingface_hub.hf_hub_download``. huggingface_hub >= 1.0 removed that
    kwarg entirely (it was deprecated years earlier in favour of
    ``token``), so a fresh install crashes with ``TypeError:
    hf_hub_download() got an unexpected keyword argument 'use_auth_token'``
    before diarization ever starts.

    This shim wraps ``hf_hub_download`` so the old kwarg is translated to
    the new one on the fly. It is idempotent: calling it more than once
    is a no-op. No effect on callers already using ``token=``.
    """
    global _HF_TOKEN_SHIM_APPLIED
    if _HF_TOKEN_SHIM_APPLIED:
        return
    import sys as _sys

    import huggingface_hub  # type: ignore[import-not-found]

    original = huggingface_hub.hf_hub_download

    def _download_with_token_alias(*args: Any, **kwargs: Any) -> Any:
        if "use_auth_token" in kwargs and "token" not in kwargs:
            kwargs["token"] = kwargs.pop("use_auth_token")
        else:
            kwargs.pop("use_auth_token", None)
        return original(*args, **kwargs)

    # Patch the canonical binding.
    huggingface_hub.hf_hub_download = _download_with_token_alias

    # Also patch any modules that already did ``from huggingface_hub import
    # hf_hub_download`` before this shim ran. pyannote.audio.core.pipeline is
    # the one we actually need, but patching every cached copy is cheap.
    for mod in list(_sys.modules.values()):
        if mod is None:
            continue
        try:
            if getattr(mod, "hf_hub_download", None) is original:
                setattr(mod, "hf_hub_download", _download_with_token_alias)
        except Exception:  # noqa: BLE001 - best-effort patch
            continue

    _HF_TOKEN_SHIM_APPLIED = True


_TORCH_LOAD_SHIM_APPLIED = False


def _apply_torch_load_shim() -> None:
    """Force ``weights_only=False`` on ``torch.load`` for pyannote checkpoints.

    PyTorch 2.6 flipped the default of ``torch.load``'s ``weights_only`` from
    ``False`` to ``True``. pyannote.audio 3.x checkpoints contain pickled
    ``TorchVersion`` objects (and potentially other non-tensor globals) that
    the weights-only unpickler refuses to load, crashing with::

        WeightsUnpickler error: Unsupported global: GLOBAL
        torch.torch_version.TorchVersion was not an allowed global by default

    We trust pyannote's checkpoints (we're downloading them from the
    official repo over HTTPS with a valid token), so forcing
    ``weights_only=False`` is safe here. Idempotent.
    """
    global _TORCH_LOAD_SHIM_APPLIED
    if _TORCH_LOAD_SHIM_APPLIED:
        return
    import torch  # type: ignore[import-not-found]

    original = torch.load

    def _load_weights_only_false(*args: Any, **kwargs: Any) -> Any:
        # Force-override: lightning_fabric.cloud_io passes weights_only=True
        # explicitly, so setdefault is not enough — we have to clobber it.
        kwargs["weights_only"] = False
        return original(*args, **kwargs)

    torch.load = _load_weights_only_false  # type: ignore[assignment]
    _TORCH_LOAD_SHIM_APPLIED = True


_SPEECHBRAIN_LAZY_SHIM_APPLIED = False


def _apply_speechbrain_lazy_shim() -> None:
    """Make speechbrain's ``LazyModule`` raise ``AttributeError`` on missing
    optional deps instead of ``ImportError``.

    pytorch_lightning's ``load_from_checkpoint`` walks ``inspect.stack()``
    to detect torch-jit context. ``inspect.getmodule`` calls
    ``hasattr(module, '__file__')`` on every module on the stack. When
    speechbrain's ``LazyModule`` wraps an optional integration that isn't
    installed (``k2_fsa``, ``nlp``, etc.) and ``__getattr__`` raises
    ``ImportError``, ``hasattr`` does **not** catch it — so the whole
    pyannote pipeline load crashes mid-init on modules it doesn't need.

    We monkey-patch ``LazyModule.ensure_module`` to convert ``ImportError``
    to ``AttributeError`` with the same message. ``hasattr`` catches
    ``AttributeError`` and returns ``False``, which is exactly what we want
    here — pretend the missing module has no attributes. Idempotent.
    """
    global _SPEECHBRAIN_LAZY_SHIM_APPLIED
    if _SPEECHBRAIN_LAZY_SHIM_APPLIED:
        return
    try:
        from speechbrain.utils import importutils as _imputils  # type: ignore[import-not-found]
    except ImportError:
        # speechbrain not installed → nothing to patch, and pyannote will
        # fail later with a clearer error. Mark applied so we don't retry.
        _SPEECHBRAIN_LAZY_SHIM_APPLIED = True
        return

    LazyModule = getattr(_imputils, "LazyModule", None)
    if LazyModule is None:
        _SPEECHBRAIN_LAZY_SHIM_APPLIED = True
        return

    original_ensure = LazyModule.ensure_module

    def _ensure_module_softfail(self, stacklevel=1):  # type: ignore[no-untyped-def]
        try:
            return original_ensure(self, stacklevel + 1)
        except ImportError as exc:  # noqa: BLE001 - we want broad catch here
            raise AttributeError(str(exc)) from exc

    LazyModule.ensure_module = _ensure_module_softfail  # type: ignore[assignment]
    _SPEECHBRAIN_LAZY_SHIM_APPLIED = True


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

    # Compatibility shim: pyannote.audio 3.x forwards ``use_auth_token`` to
    # ``huggingface_hub.hf_hub_download``, but hf_hub >= 1.0 removed that
    # kwarg in favour of ``token``. Rename it in-flight so the call lands
    # cleanly regardless of which hf_hub version is installed. Safe to
    # re-apply; _apply_hf_token_shim is idempotent.
    _apply_hf_token_shim()
    _apply_torch_load_shim()
    _apply_speechbrain_lazy_shim()

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
