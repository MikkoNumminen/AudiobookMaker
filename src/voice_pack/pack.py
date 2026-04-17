"""Voice-pack on-disk artefact format.

A voice pack is a directory that bundles everything needed to speak in a
cloned voice: a human-readable ``meta.yaml``, a short preview ``sample.wav``,
and tier-specific assets (LoRA adapter weights for the ``full_lora`` and
``reduced_lora`` tiers, or a reference clip for the ``few_shot`` tier).

This module defines the dataclasses, validation rules, and filesystem helpers
for discovering, loading, validating, and installing voice packs. The GUI
scans a packs root, surfaces valid packs to the user, and lists broken packs
separately via :func:`validate_pack_dir`.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

VOICE_PACK_FORMAT_VERSION: int = 1
VALID_TIERS: tuple[str, ...] = ("full_lora", "reduced_lora", "few_shot")

_REQUIRED_META_KEYS: tuple[str, ...] = (
    "name",
    "language",
    "tier",
    "tier_reason",
    "total_source_minutes",
)


class VoicePackError(Exception):
    """Raised by load/install helpers when a voice pack is malformed."""


@dataclass
class VoicePackMeta:
    """Structured view of a voice pack's ``meta.yaml`` file."""

    name: str
    language: str
    tier: str
    tier_reason: str
    total_source_minutes: float
    emotion_coverage: dict[str, int] = field(default_factory=dict)
    base_model: str = "chatterbox-multilingual"
    format_version: int = VOICE_PACK_FORMAT_VERSION
    created_at: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a plain ``dict`` suitable for YAML serialisation."""

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VoicePackMeta":
        """Build a :class:`VoicePackMeta` from a parsed YAML mapping.

        Missing optional keys fall back to their dataclass defaults. Missing
        required keys raise :class:`KeyError` with a clear name.
        """

        for key in _REQUIRED_META_KEYS:
            if key not in data:
                raise KeyError(key)
        return cls(
            name=str(data["name"]),
            language=str(data["language"]),
            tier=str(data["tier"]),
            tier_reason=str(data["tier_reason"]),
            total_source_minutes=float(data["total_source_minutes"]),
            emotion_coverage=dict(data.get("emotion_coverage") or {}),
            base_model=str(data.get("base_model", "chatterbox-multilingual")),
            format_version=int(data.get("format_version", VOICE_PACK_FORMAT_VERSION)),
            created_at=str(data.get("created_at", "")),
            notes=str(data.get("notes", "")),
        )


@dataclass
class VoicePack:
    """A loaded voice pack: its root directory plus parsed metadata."""

    root: Path
    meta: VoicePackMeta

    @property
    def sample_path(self) -> Path:
        """Path to the preview ``sample.wav`` clip."""

        return self.root / "sample.wav"

    @property
    def adapter_path(self) -> Path:
        """Path to the LoRA adapter weights (tier-dependent)."""

        return self.root / "adapter.pt"

    @property
    def reference_path(self) -> Path:
        """Path to the few-shot reference clip (tier-dependent)."""

        return self.root / "reference.wav"

    @property
    def display_name(self) -> str:
        """Human-readable name suitable for the GUI voice picker."""

        return self.meta.name


def default_voice_packs_root() -> Path:
    """Return the default voice-packs directory under the user's home folder."""

    return Path.home() / ".audiobookmaker" / "voice_packs"


def _meta_path(pack_dir: Path) -> Path:
    return pack_dir / "meta.yaml"


def validate_pack_dir(pack_dir: str | Path) -> list[str]:
    """Return a list of validation issues. Empty list means the pack is valid.

    Issues are short human-readable strings (``'meta.yaml missing'``,
    ``'adapter.pt missing for tier=full_lora'``, ...). Unlike :func:`load_pack`,
    this never raises for malformed packs; it returns every problem it finds
    so a health-check UI can display them all at once.
    """

    pack_dir = Path(pack_dir)
    issues: list[str] = []

    if not pack_dir.exists():
        issues.append(f"pack directory missing: {pack_dir}")
        return issues
    if not pack_dir.is_dir():
        issues.append(f"pack path is not a directory: {pack_dir}")
        return issues

    meta_path = _meta_path(pack_dir)
    if not meta_path.exists():
        issues.append("meta.yaml missing")
        # Without meta we can still check sample.wav, but not tier-specific assets.
        if not (pack_dir / "sample.wav").exists():
            issues.append("sample.wav missing")
        return issues

    try:
        raw = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        issues.append(f"meta.yaml parse error: {exc}")
        return issues

    if not isinstance(raw, dict):
        issues.append("meta.yaml is not a mapping")
        return issues

    try:
        meta = VoicePackMeta.from_dict(raw)
    except KeyError as exc:
        issues.append(f"meta.yaml missing required key: {exc.args[0]}")
        return issues
    except (TypeError, ValueError) as exc:
        issues.append(f"meta.yaml has bad value: {exc}")
        return issues

    if meta.tier not in VALID_TIERS:
        issues.append(
            f"unknown tier {meta.tier!r}; expected one of {', '.join(VALID_TIERS)}"
        )

    if meta.format_version > VOICE_PACK_FORMAT_VERSION:
        issues.append(
            f"voice pack format version {meta.format_version} is newer than "
            f"supported version {VOICE_PACK_FORMAT_VERSION}; update the app"
        )

    if not (pack_dir / "sample.wav").exists():
        issues.append("sample.wav missing")

    if meta.tier in ("full_lora", "reduced_lora"):
        if not (pack_dir / "adapter.pt").exists():
            issues.append(f"adapter.pt missing for tier={meta.tier}")
    elif meta.tier == "few_shot":
        if not (pack_dir / "reference.wav").exists():
            issues.append("reference.wav missing for tier=few_shot")

    return issues


def load_pack(pack_dir: str | Path) -> VoicePack:
    """Load and validate a voice pack directory.

    Raises
    ------
    FileNotFoundError
        If ``pack_dir`` does not exist or is not a directory.
    VoicePackError
        If ``meta.yaml`` is missing, unparseable, or describes an invalid
        / future-versioned / tier-incomplete pack.
    """

    pack_dir = Path(pack_dir)
    if not pack_dir.exists() or not pack_dir.is_dir():
        raise FileNotFoundError(f"voice pack directory not found: {pack_dir}")

    meta_path = _meta_path(pack_dir)
    if not meta_path.exists():
        raise VoicePackError(f"meta.yaml missing in {pack_dir}")

    try:
        raw = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise VoicePackError(f"meta.yaml parse error in {pack_dir}: {exc}") from exc

    if not isinstance(raw, dict):
        raise VoicePackError(f"meta.yaml in {pack_dir} is not a mapping")

    try:
        meta = VoicePackMeta.from_dict(raw)
    except KeyError as exc:
        missing = exc.args[0] if exc.args else "?"
        raise VoicePackError(
            f"meta.yaml in {pack_dir} missing required key: {missing}"
        ) from exc
    except (TypeError, ValueError) as exc:
        raise VoicePackError(f"meta.yaml in {pack_dir} has bad value: {exc}") from exc

    if meta.tier not in VALID_TIERS:
        raise VoicePackError(
            f"unknown tier {meta.tier!r} in {pack_dir}; "
            f"expected one of {', '.join(VALID_TIERS)}"
        )

    if meta.format_version > VOICE_PACK_FORMAT_VERSION:
        raise VoicePackError(
            f"voice pack format version {meta.format_version} is newer than "
            f"supported version {VOICE_PACK_FORMAT_VERSION}; update the app"
        )

    if not (pack_dir / "sample.wav").exists():
        raise VoicePackError(f"sample.wav missing in {pack_dir}")

    if meta.tier in ("full_lora", "reduced_lora"):
        if not (pack_dir / "adapter.pt").exists():
            raise VoicePackError(
                f"adapter.pt missing for tier={meta.tier} in {pack_dir}"
            )
    elif meta.tier == "few_shot":
        if not (pack_dir / "reference.wav").exists():
            raise VoicePackError(
                f"reference.wav missing for tier=few_shot in {pack_dir}"
            )

    return VoicePack(root=pack_dir, meta=meta)


def list_packs(root: str | Path | None = None) -> list[VoicePack]:
    """Scan a voice-packs root and return every valid pack inside.

    Invalid pack subdirectories are skipped silently; the UI surfaces them
    via a separate :func:`validate_pack_dir` health-check pass. Returned
    packs are sorted by lowercased display name.
    """

    root_path = Path(root) if root is not None else default_voice_packs_root()
    if not root_path.exists() or not root_path.is_dir():
        return []

    packs: list[VoicePack] = []
    for child in root_path.iterdir():
        if not child.is_dir():
            continue
        try:
            packs.append(load_pack(child))
        except (FileNotFoundError, VoicePackError):
            continue

    packs.sort(key=lambda p: p.meta.name.lower())
    return packs


_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9_-]+")
_SLUG_COLLAPSE_RE = re.compile(r"_+")


def _slugify(name: str) -> str:
    """Turn a display name into a filesystem-safe folder slug.

    Lowercases, replaces any run of non-``[a-z0-9_-]`` characters with a
    single underscore, collapses repeats, and strips leading/trailing
    underscores. Falls back to ``'voice-pack'`` if the result is empty.
    """

    lowered = name.strip().lower()
    cleaned = _SLUG_CLEAN_RE.sub("_", lowered)
    collapsed = _SLUG_COLLAPSE_RE.sub("_", cleaned).strip("_")
    return collapsed or "voice-pack"


def install_pack(
    source_dir: str | Path,
    root: str | Path | None = None,
    *,
    rename_to: str | None = None,
    overwrite: bool = False,
) -> VoicePack:
    """Copy a voice pack from ``source_dir`` into ``root`` and load it.

    The destination folder name is ``rename_to`` if given, otherwise a slug
    derived from ``meta.name``. If the destination already exists and
    ``overwrite`` is False, :class:`FileExistsError` is raised. If the
    source is not a valid pack, :class:`VoicePackError` is raised.
    """

    source_path = Path(source_dir)
    issues = validate_pack_dir(source_path)
    if issues:
        raise VoicePackError(
            f"source voice pack at {source_path} is invalid: {'; '.join(issues)}"
        )

    # Source is valid, so load_pack succeeds here and gives us meta.name.
    source_pack = load_pack(source_path)

    root_path = Path(root) if root is not None else default_voice_packs_root()
    root_path.mkdir(parents=True, exist_ok=True)

    slug = rename_to if rename_to is not None else _slugify(source_pack.meta.name)
    if not slug:
        slug = "voice-pack"
    destination = root_path / slug

    if destination.exists():
        if not overwrite:
            raise FileExistsError(
                f"voice pack destination already exists: {destination}"
            )
        shutil.rmtree(destination)

    shutil.copytree(source_path, destination)
    return load_pack(destination)
