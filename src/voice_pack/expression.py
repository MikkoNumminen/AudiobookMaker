"""Per-sentence expression control for voice-cloning synthesis.

This module defines the data model + parser used to push a voice-cloned
Chatterbox voice into specific emotional modes ("shout", "whisper", "calm")
on a per-sentence basis. The idea: a clone trained on a full emotional
range can be nudged at synthesis time by adjusting Chatterbox's
``exaggeration`` and ``cfg_weight`` knobs.

The input text can carry simple markup so a user can annotate a book:

    {{expr:shout}}
    "STOP THIS MADNESS!" he cried.
    {{expr:default}}
    He drew in a breath.
    {{expr:whisper exag=0.3 cfg=0.4}}
    "I know what you did," she said.

A directive line sticks until another directive replaces it;
``{{expr:default}}`` clears state back to the plan default.

Public surface:

* ``ExpressionPreset`` -- a named ``(exaggeration, cfg_weight)`` bundle.
* ``BUILT_IN_PRESETS`` -- whisper, calm, neutral, intense, shout.
* ``ExpressionDirective`` -- a per-sentence override.
* ``ExpressionPlan`` -- collection of directives + default; resolves values
  for a given sentence index.
* ``parse_markup`` -- strip directives from text, emit a plan whose sentence
  indices line up with the cleaned output.
* ``is_valid_preset_name`` -- validator for user-defined preset names.

The module has no I/O and no external deps; it is safe to import anywhere.
It intentionally does not import from ``align`` -- the minimal sentence
split lives here so the two modules stay decoupled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


_EXAG_MIN = 0.0
_EXAG_MAX = 2.0
_CFG_MIN = 0.0
_CFG_MAX = 1.0


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* into the inclusive ``[lo, hi]`` range."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


@dataclass(frozen=True)
class ExpressionPreset:
    """Named bundle of Chatterbox expression knobs.

    ``exaggeration`` is clamped to ``[0.0, 2.0]`` and ``cfg_weight`` to
    ``[0.0, 1.0]`` at construction. Out-of-range values are clamped
    silently rather than raising.
    """

    name: str
    exaggeration: float
    cfg_weight: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "exaggeration", _clamp(float(self.exaggeration), _EXAG_MIN, _EXAG_MAX)
        )
        object.__setattr__(
            self, "cfg_weight", _clamp(float(self.cfg_weight), _CFG_MIN, _CFG_MAX)
        )

    def to_dict(self) -> dict:
        """Return a plain-dict representation suitable for serialization."""
        return {
            "name": self.name,
            "exaggeration": self.exaggeration,
            "cfg_weight": self.cfg_weight,
        }


BUILT_IN_PRESETS: dict[str, ExpressionPreset] = {
    "whisper": ExpressionPreset("whisper", exaggeration=0.2, cfg_weight=0.35),
    "calm": ExpressionPreset("calm", exaggeration=0.4, cfg_weight=0.4),
    "neutral": ExpressionPreset("neutral", exaggeration=0.6, cfg_weight=0.5),
    "intense": ExpressionPreset("intense", exaggeration=0.9, cfg_weight=0.6),
    "shout": ExpressionPreset("shout", exaggeration=1.2, cfg_weight=0.7),
}


@dataclass(frozen=True)
class ExpressionDirective:
    """Per-sentence override record.

    ``preset`` names a preset (built-in or custom). ``exaggeration`` and
    ``cfg_weight``, when set, win over the preset's values on a per-knob
    basis. Unknown preset names are tolerated and fall back to
    ``neutral`` at resolve time.
    """

    sentence_index: int
    preset: str | None = None
    exaggeration: float | None = None
    cfg_weight: float | None = None

    def to_dict(self) -> dict:
        """Return a plain-dict representation suitable for serialization."""
        return {
            "sentence_index": self.sentence_index,
            "preset": self.preset,
            "exaggeration": self.exaggeration,
            "cfg_weight": self.cfg_weight,
        }


@dataclass
class ExpressionPlan:
    """Collection of per-sentence directives + default preset.

    Call :meth:`resolve_for` with a sentence index to get the effective
    ``(exaggeration, cfg_weight)`` tuple. Resolution precedence:

    1. Matching directive with explicit knob value.
    2. Matching directive's preset.
    3. Plan ``default_preset``.

    If multiple directives share the same ``sentence_index`` the last one
    wins.
    """

    directives: list[ExpressionDirective] = field(default_factory=list)
    default_preset: str = "neutral"
    custom_presets: dict[str, ExpressionPreset] = field(default_factory=dict)

    def presets(self) -> dict[str, ExpressionPreset]:
        """Return merged preset dict; custom entries shadow built-ins."""
        merged: dict[str, ExpressionPreset] = dict(BUILT_IN_PRESETS)
        merged.update(self.custom_presets)
        return merged

    def _lookup_preset(self, name: str | None) -> ExpressionPreset:
        """Return preset by name, falling back to neutral if unknown/None."""
        presets = self.presets()
        if name is not None and name in presets:
            return presets[name]
        if self.default_preset in presets:
            return presets[self.default_preset]
        return BUILT_IN_PRESETS["neutral"]

    def resolve_for(self, sentence_index: int) -> tuple[float, float]:
        """Return ``(exaggeration, cfg_weight)`` for *sentence_index*.

        Unknown preset names are tolerated -- they fall back to
        ``neutral`` (or the plan default, if that is known) so a typo in
        markup never crashes synthesis.
        """
        # last-wins: scan forward, keep most recent match
        match: ExpressionDirective | None = None
        for d in self.directives:
            if d.sentence_index == sentence_index:
                match = d

        if match is None:
            base = self._lookup_preset(self.default_preset)
            return (
                _clamp(base.exaggeration, _EXAG_MIN, _EXAG_MAX),
                _clamp(base.cfg_weight, _CFG_MIN, _CFG_MAX),
            )

        # A directive may reference an unknown preset; fall back silently.
        base = self._lookup_preset(match.preset)

        exag = base.exaggeration
        cfg = base.cfg_weight
        if match.exaggeration is not None:
            exag = match.exaggeration
        if match.cfg_weight is not None:
            cfg = match.cfg_weight
        return (
            _clamp(float(exag), _EXAG_MIN, _EXAG_MAX),
            _clamp(float(cfg), _CFG_MIN, _CFG_MAX),
        )

    def to_dict(self) -> dict:
        """Return a plain-dict representation suitable for serialization."""
        return {
            "directives": [d.to_dict() for d in self.directives],
            "default_preset": self.default_preset,
            "custom_presets": {k: v.to_dict() for k, v in self.custom_presets.items()},
        }


_DIRECTIVE_RE = re.compile(r"^\{\{expr:(\S+?)(?:\s+([^}]*))?\}\}\s*$")
# Loose match for malformed variants like ``{{expr:}}`` or ``{{expr: }}``;
# these lines are dropped from cleaned text but do not change state.
_DIRECTIVE_LOOSE_RE = re.compile(r"^\{\{expr:[^}]*\}\}\s*$")
_KWARG_RE = re.compile(r"^(\w+)=([-+]?\d*\.?\d+)$")
_PRESET_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# Minimal sentence splitter: split on .!? followed by whitespace or EOS.
# Duplicates what ``align.split_sentences`` does so the two modules stay
# decoupled -- a tiny copy is cheaper than a cross-import.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    """Split *text* into a list of non-empty, stripped sentences."""
    if not text or not text.strip():
        return []
    pieces = _SENTENCE_SPLIT_RE.split(text.strip())
    return [p.strip() for p in pieces if p and p.strip()]


def is_valid_preset_name(name: str) -> bool:
    """Return True if *name* is a legal preset identifier.

    Rule: lowercase ASCII letters/digits/underscore, starting with a
    letter or underscore. Matches ``^[a-z_][a-z0-9_]*$``. Used when
    validating user-defined preset names from ``meta.yaml``.
    """
    if not isinstance(name, str) or not name:
        return False
    return bool(_PRESET_NAME_RE.match(name))


def _parse_kwargs(kwargs_str: str | None) -> tuple[float | None, float | None]:
    """Return ``(exag_override, cfg_override)`` parsed from kwargs_str.

    Unknown keys are ignored. Invalid numbers are ignored. Only ``exag``
    and ``cfg`` are recognised.
    """
    if not kwargs_str:
        return (None, None)
    exag: float | None = None
    cfg: float | None = None
    for tok in kwargs_str.strip().split():
        m = _KWARG_RE.match(tok)
        if not m:
            continue
        key = m.group(1).lower()
        try:
            value = float(m.group(2))
        except ValueError:
            continue
        if key == "exag":
            exag = value
        elif key == "cfg":
            cfg = value
        # unknown keys silently ignored
    return (exag, cfg)


def parse_markup(text: str) -> tuple[str, ExpressionPlan]:
    """Parse expression markup out of *text*.

    Returns a tuple ``(cleaned_text, plan)``. ``cleaned_text`` is the
    original input with all ``{{expr:...}}`` lines removed; blank lines
    and other line breaks are preserved so downstream paragraphing still
    works. ``plan.directives`` carry sentence indices that line up with
    ``_split_sentences(cleaned_text)``.

    Design: a directive sticks until another directive replaces it. To
    clear state back to the plan default, use ``{{expr:default}}``.
    """
    if text is None:
        return "", ExpressionPlan(default_preset="neutral")

    # Current sticky state, updated every time we see a directive line.
    cur_preset: str | None = None
    cur_exag: float | None = None
    cur_cfg: float | None = None

    cleaned_lines: list[str] = []
    # For each non-directive, non-blank source line we record the sticky
    # state as it stood when we saw that line. Then we split the cleaned
    # text into sentences and assign state per sentence by walking the
    # cleaned lines in order.
    line_states: list[tuple[str | None, float | None, float | None]] = []

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        m = _DIRECTIVE_RE.match(stripped)
        if m:
            name = (m.group(1) or "").strip()
            kwargs_str = m.group(2)
            if name == "default":
                cur_preset = None
                cur_exag = None
                cur_cfg = None
            else:
                cur_preset = name
                exag, cfg = _parse_kwargs(kwargs_str)
                cur_exag = exag
                cur_cfg = cfg
            # directive line is stripped from cleaned text
            continue

        if _DIRECTIVE_LOOSE_RE.match(stripped):
            # malformed directive -- drop the line, don't touch state
            continue

        cleaned_lines.append(raw_line)
        if raw_line.strip():
            line_states.append((cur_preset, cur_exag, cur_cfg))

    cleaned_text = "\n".join(cleaned_lines)

    # Walk cleaned non-blank lines in order; split each into sentences;
    # pair each sentence with the state recorded for its source line.
    directives: list[ExpressionDirective] = []
    sentence_idx = 0
    state_iter = iter(line_states)
    for cleaned_line in cleaned_lines:
        if not cleaned_line.strip():
            continue
        state = next(state_iter, (None, None, None))
        preset, exag, cfg = state
        for _sent in _split_sentences(cleaned_line):
            if preset is not None or exag is not None or cfg is not None:
                directives.append(
                    ExpressionDirective(
                        sentence_index=sentence_idx,
                        preset=preset,
                        exaggeration=exag,
                        cfg_weight=cfg,
                    )
                )
            sentence_idx += 1

    plan = ExpressionPlan(directives=directives, default_preset="neutral")
    return cleaned_text, plan


__all__ = [
    "BUILT_IN_PRESETS",
    "ExpressionDirective",
    "ExpressionPlan",
    "ExpressionPreset",
    "is_valid_preset_name",
    "parse_markup",
]
