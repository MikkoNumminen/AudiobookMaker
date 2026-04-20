"""Murre puhekieli → kirjakieli normalizer (CTranslate2 runtime wrapper).

Murre (https://github.com/mikahama/murre) is a seq2seq model that maps
spoken/colloquial Finnish to standard written Finnish. It substantially
improves TTS rendering of dialogue, internet text, and Finnish meme
culture — chunks like ``mä oon menos kauppaan`` come out as ``minä olen
menossa kauppaan``, which Chatterbox-Finnish reads correctly.

The model ships as an OpenNMT-py 2.x checkpoint, which cannot be loaded
on Windows + Python 3.11 (the torchtext.Field dependency was removed
in March 2023). Workaround: run ``scripts/convert_murre_model.py`` once
inside Docker / WSL with Python 3.9, which writes a CTranslate2 model
directory to ``.local/murre_models_ct2/``. This module then loads the
converted model with the modern ``ctranslate2`` package and never
imports torchtext or OpenNMT-py at runtime.

If the converted model directory does not exist, or if ``ctranslate2``
is not installed, :func:`normalize` is a no-op that returns its input
unchanged. A single warning is logged the first time the wrapper is
asked to normalize text without a model.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_DIR = REPO_ROOT / ".local" / "murre_models_ct2"
DEFAULT_MARKER = "murre_ct2.json"

# Murre's training data was chunked into groups of 3 whitespace-separated
# tokens joined by a literal " _ " token. Inference must use the same
# chunk size and joiner so the model sees the format it expects. See
# Murre's normalize_sentences() in mikahama/murre for the canonical
# implementation.
_CHUNK_SIZE = 3
_CHUNK_JOINER = "_"

_log = logging.getLogger(__name__)
_warned_missing = False
_lock = threading.Lock()
_translator = None  # ctranslate2.Translator | None
_src_vocab: Optional[list[str]] = None
_tgt_vocab: Optional[list[str]] = None


def _warn_once_missing(reason: str) -> None:
    """Log a warning the first time normalize() is asked to act with no model."""
    global _warned_missing
    if _warned_missing:
        return
    _warned_missing = True
    _log.warning(
        "Murre normalizer disabled: %s. Spoken-Finnish (puhekieli) input "
        "will be passed through to the TTS engine unchanged. To enable, "
        "follow scripts/convert_murre_model.py instructions.",
        reason,
    )


def is_available(model_dir: Path = DEFAULT_MODEL_DIR) -> bool:
    """Return True iff a converted Murre model exists AND ctranslate2 is importable.

    Cheap check — does NOT load the model. Safe to call in hot paths.
    """
    marker = model_dir / DEFAULT_MARKER
    if not marker.exists():
        return False
    try:
        import ctranslate2  # noqa: F401
    except ImportError:
        return False
    return True


def _load(model_dir: Path) -> bool:
    """Lazy-load the CTranslate2 translator and vocab files. Idempotent.

    Returns True on success, False if anything is missing. Holds an
    internal lock so concurrent calls do not double-load.
    """
    global _translator, _src_vocab, _tgt_vocab
    if _translator is not None:
        return True

    with _lock:
        if _translator is not None:
            return True

        marker = model_dir / DEFAULT_MARKER
        if not marker.exists():
            _warn_once_missing(f"converted model not found at {model_dir}")
            return False

        try:
            import ctranslate2  # type: ignore
        except ImportError:
            _warn_once_missing(
                "ctranslate2 is not installed (try: pip install ctranslate2)"
            )
            return False

        try:
            _translator = ctranslate2.Translator(str(model_dir), device="cpu")
        except (RuntimeError, OSError) as exc:
            _warn_once_missing(f"ctranslate2 failed to load model: {exc}")
            return False

        # Vocab files are optional — only used if the integration ever
        # needs explicit token IDs. ctranslate2's translate_batch
        # accepts plain string tokens directly.
        src_path = model_dir / "vocab.src.txt"
        tgt_path = model_dir / "vocab.tgt.txt"
        if src_path.exists():
            _src_vocab = src_path.read_text(encoding="utf-8").splitlines()
        if tgt_path.exists():
            _tgt_vocab = tgt_path.read_text(encoding="utf-8").splitlines()

    return True


def _chunk_tokens(tokens: list[str], chunk_size: int = _CHUNK_SIZE) -> list[list[str]]:
    """Split ``tokens`` into consecutive groups of ``chunk_size`` tokens.

    The last chunk may be shorter than ``chunk_size`` (Murre handles
    short tails by padding internally — we do not).
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    return [tokens[i:i + chunk_size] for i in range(0, len(tokens), chunk_size)]


def _build_input_chunks(tokens: list[str]) -> list[list[str]]:
    """Produce the per-chunk token lists CTranslate2 expects as input.

    Murre's training format inserts a literal ``_`` token between the
    whitespace-tokenized words of each chunk. So the chunk
    ``["mä", "oon", "menos"]`` becomes the token sequence
    ``["mä", "_", "oon", "_", "menos"]`` for the encoder.
    """
    out: list[list[str]] = []
    for chunk in _chunk_tokens(tokens):
        if not chunk:
            continue
        joined: list[str] = []
        for i, tok in enumerate(chunk):
            if i > 0:
                joined.append(_CHUNK_JOINER)
            joined.append(tok)
        out.append(joined)
    return out


def _dechunk(translated_chunks: list[list[str]]) -> str:
    """Join translated chunks back into a single string.

    Each translated chunk is a list of tokens including the ``_``
    separators emitted by the model. Replace ``_`` with a space inside
    each chunk; join chunks with a space.
    """
    parts: list[str] = []
    for chunk_tokens in translated_chunks:
        joined = " ".join(chunk_tokens)
        # The model emits "_" tokens to mark the original whitespace
        # boundaries inside the chunk. Replace them with a single space.
        joined = joined.replace(f" {_CHUNK_JOINER} ", " ")
        # Edge case: chunk starts/ends with the joiner.
        if joined.startswith(f"{_CHUNK_JOINER} "):
            joined = joined[2:]
        if joined.endswith(f" {_CHUNK_JOINER}"):
            joined = joined[:-2]
        parts.append(joined)
    return " ".join(parts).strip()


def normalize(text: str, *, model_dir: Path = DEFAULT_MODEL_DIR) -> str:
    """Normalize spoken Finnish text to written Finnish.

    Returns the input unchanged if the converted Murre model or the
    ``ctranslate2`` package is missing. Whitespace is collapsed and
    leading/trailing whitespace stripped from the return value when the
    model runs; if the no-op fallback fires, the input is returned
    verbatim.
    """
    if not text or not text.strip():
        return text
    if not _load(model_dir):
        return text

    assert _translator is not None  # for type checkers — _load enforced this
    tokens = text.split()
    chunks = _build_input_chunks(tokens)
    if not chunks:
        return text

    results = _translator.translate_batch(chunks)
    translated: list[list[str]] = []
    for r in results:
        if not r.hypotheses:
            translated.append([])
            continue
        translated.append(list(r.hypotheses[0]))
    return _dechunk(translated)


def reset_for_tests() -> None:
    """Reset the module-level cache. Test-only."""
    global _translator, _src_vocab, _tgt_vocab, _warned_missing
    with _lock:
        _translator = None
        _src_vocab = None
        _tgt_vocab = None
        _warned_missing = False
