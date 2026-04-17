"""Tiny shared helper for lazy-loading YAML lookup tables from ``data/``.

The TTS normalizers pull their lexicons from YAML so non-developers can
edit them without touching Python. This module centralises the
"open a file, safe_load, cache the result" bookkeeping the normalizers
would otherwise repeat in each module. The pattern mirrors
``src/fi_loanwords.py`` but without the Lexicon dataclass — callers
decide how to shape the raw YAML into whatever runtime structure they
need.

The loader:
    * uses ``yaml.safe_load`` (never ``yaml.load``),
    * caches by filename inside this module,
    * degrades gracefully: missing file or missing PyYAML -> returns
      ``None`` and logs one warning.

Callers that need a strongly-typed fallback (empty set, empty dict) can
substitute the ``None`` themselves — keeping the helper dumb keeps the
behaviour predictable across modules.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"

_cache: dict[str, Any] = {}
_warned: set[str] = set()


def load_yaml(name: str) -> Any | None:
    """Load ``data/<name>.yaml`` and cache the parsed structure.

    Returns ``None`` if PyYAML is missing or the file is unreadable.
    Emits at most one warning per filename per process.
    """
    if name in _cache:
        return _cache[name]

    try:
        import yaml  # type: ignore
    except ImportError:
        if name not in _warned:
            logger.warning(
                "PyYAML is not installed — %s.yaml cannot be loaded.",
                name,
            )
            _warned.add(name)
        _cache[name] = None
        return None

    path = _DATA_DIR / f"{name}.yaml"
    if not path.exists():
        if name not in _warned:
            logger.warning("Lookup table not found: %s", path)
            _warned.add(name)
        _cache[name] = None
        return None

    try:
        with open(path, encoding="utf-8") as fh:
            parsed = yaml.safe_load(fh)
    except Exception as exc:  # noqa: BLE001
        if name not in _warned:
            logger.warning("Failed to parse %s (%s).", path, exc)
            _warned.add(name)
        _cache[name] = None
        return None

    _cache[name] = parsed
    return parsed
