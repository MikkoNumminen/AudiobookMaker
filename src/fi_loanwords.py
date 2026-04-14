"""Finnish loanword respelling module for AudiobookMaker.

Pass I of the Finnish TTS normalizer: fixes mispronunciations of common
loanword patterns (-ismi, -tio) and substitutes foreign names and Latin
phrases with phonetically correct Finnish spellings.

The lexicon is loaded lazily from ``data/fi_loanwords.yaml`` on first call
and cached for the lifetime of the process. If the YAML file is missing or
PyYAML is not installed the module degrades gracefully: the public function
returns the input text unchanged and a single warning is logged.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

_YAML_PATH = Path(__file__).parent.parent / "data" / "fi_loanwords.yaml"


@dataclass
class Lexicon:
    """Parsed contents of fi_loanwords.yaml."""

    ismi_stems: frozenset[str] = field(default_factory=frozenset)
    tio_stems: frozenset[str] = field(default_factory=frozenset)
    # Sorted longest-first for greedy phrase matching.
    latin_phrases: list[tuple[str, str]] = field(default_factory=list)
    foreign_names: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Lazy loader
# ---------------------------------------------------------------------------

_lexicon_cache: Lexicon | None = None
_load_attempted: bool = False


def _load_lexicon() -> Lexicon | None:
    """Load and cache the loanword lexicon from YAML.

    Returns ``None`` if the file is missing or PyYAML is unavailable.
    Logs a single warning in either case.
    """
    global _lexicon_cache, _load_attempted
    if _load_attempted:
        return _lexicon_cache
    _load_attempted = True

    try:
        import yaml  # type: ignore
    except ImportError:
        logger.warning(
            "PyYAML is not installed — Pass I (loanword respelling) is disabled. "
            "Install pyyaml to enable it."
        )
        return None

    if not _YAML_PATH.exists():
        logger.warning(
            "Loanword lexicon not found at %s — Pass I (loanword respelling) is "
            "disabled.",
            _YAML_PATH,
        )
        return None

    try:
        with open(_YAML_PATH, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to parse %s (%s) — Pass I (loanword respelling) is disabled.",
            _YAML_PATH,
            exc,
        )
        return None

    if not raw or not isinstance(raw, dict):
        # Empty or non-mapping YAML — return empty Lexicon (no-op).
        _lexicon_cache = Lexicon()
        return _lexicon_cache

    ismi_raw = raw.get("ismi_stems") or []
    tio_raw = raw.get("tio_stems") or []
    latin_raw = raw.get("latin_phrases") or {}
    names_raw = raw.get("foreign_names") or {}

    # Sort latin phrases longest-first so longer phrases are tried before
    # any shorter prefix phrase (e.g. "usus modernus pandectarum" before
    # "usus modernus").
    latin_sorted = sorted(
        ((k, v) for k, v in latin_raw.items()),
        key=lambda kv: len(kv[0]),
        reverse=True,
    )

    _lexicon_cache = Lexicon(
        ismi_stems=frozenset(str(s).lower() for s in ismi_raw),
        tio_stems=frozenset(str(s).lower() for s in tio_raw),
        latin_phrases=latin_sorted,
        foreign_names={str(k): str(v) for k, v in names_raw.items()},
    )
    return _lexicon_cache


# ---------------------------------------------------------------------------
# Sub-pass helpers
# ---------------------------------------------------------------------------

_ISMI_RE = re.compile(r"\b(\w+?)ismi(\w*)\b", re.IGNORECASE)
_TIO_RE = re.compile(r"\b(\w+?)tio(\w*)\b", re.IGNORECASE)


def _respell_ismi(text: str, stems: frozenset[str]) -> str:
    """Insert a hyphen between 'is' and 'mi' for whitelisted stems."""
    if not stems:
        return text

    def _sub(m: re.Match) -> str:
        stem = m.group(1)
        suffix = m.group(2)
        if stem.lower() not in stems:
            return m.group(0)
        # Preserve original casing of the stem; append -mi + suffix.
        return f"{stem}is-mi{suffix}"

    return _ISMI_RE.sub(_sub, text)


def _respell_tio(text: str, stems: frozenset[str]) -> str:
    """Insert a hyphen between the stem and 'tio' for whitelisted stems."""
    if not stems:
        return text

    def _sub(m: re.Match) -> str:
        stem = m.group(1)
        suffix = m.group(2)
        if stem.lower() not in stems:
            return m.group(0)
        return f"{stem}-tio{suffix}"

    return _TIO_RE.sub(_sub, text)


def _respell_latin_phrases(text: str, phrases: list[tuple[str, str]]) -> str:
    """Replace Latin phrases with Finnish-phonetic equivalents.

    Match is case-insensitive; the replacement is always the literal value
    from the YAML (no case-preservation). Phrases are tried longest-first.
    """
    if not phrases:
        return text

    for latin, replacement in phrases:
        # Build a case-insensitive literal match with word boundaries.
        # Word boundaries work for ASCII-letter-bounded phrases.
        pattern = re.compile(r"(?<!\w)" + re.escape(latin) + r"(?!\w)", re.IGNORECASE)
        text = pattern.sub(replacement, text)
    return text


def _respell_foreign_names(text: str, names: dict[str, str]) -> str:
    """Replace foreign proper names with Finnish-phonetic equivalents.

    Exact-word substitution: only standalone tokens are matched. Case of the
    lookup key is respected (keys in the YAML should match the expected input
    capitalisation exactly, e.g. ``Wittenberg``). This means declined forms
    such as ``Leidenissä`` are NOT substituted — a known limitation documented
    in the design notes.
    """
    if not names:
        return text

    # Build one alternation sorted longest-first to avoid partial matches.
    sorted_keys = sorted(names.keys(), key=len, reverse=True)
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in sorted_keys) + r")\b"
    )

    def _sub(m: re.Match) -> str:
        return names[m.group(1)]

    return pattern.sub(_sub, text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_loanword_respellings(text: str) -> str:
    """Apply Pass I loanword respellings to Finnish text.

    Runs four sub-passes in order:
    1. Foreign name substitution (exact word, case-sensitive key lookup)
    2. Latin phrase substitution (case-insensitive, longest-first)
    3. ``-ismi`` stem respelling (insert hyphen: ``humanis-mi``)
    4. ``-tio`` stem respelling (insert hyphen: ``instituu-tio``)

    If the lexicon cannot be loaded (missing file, missing PyYAML, parse
    error) the function returns *text* unchanged — no exception is raised.
    """
    if not text:
        return text

    lex = _load_lexicon()
    if lex is None:
        return text

    text = _respell_foreign_names(text, lex.foreign_names)
    text = _respell_latin_phrases(text, lex.latin_phrases)
    text = _respell_ismi(text, lex.ismi_stems)
    text = _respell_tio(text, lex.tio_stems)
    return text
