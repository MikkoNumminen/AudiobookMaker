"""Voice pack packaging CLI — stage 4 of the voice-cloning pipeline.

Given a completed training run and a chosen quality tier, this thin utility
assembles a ready-to-install voice pack directory on disk. The heavy lifting
(training, reference-clip extraction, tier assignment) happens in earlier
stages; this script only moves files into the canonical layout and writes
``meta.yaml``.

On-disk layout produced::

    <out_dir>/<slug>/
      meta.yaml
      sample.wav
      adapter.pt     # only for full_lora / reduced_lora
      reference.wav  # only for few_shot

The :mod:`src.voice_pack.pack` module owns the :class:`VoicePackMeta`
dataclass and the loader used by the GUI; this CLI re-uses both so the
format stays in lockstep with what the app can actually read back.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import sys
import unicodedata
from pathlib import Path
from typing import Sequence

import yaml

# Make ``src.voice_pack`` importable when running this script directly
# (scripts/ is not a package and is invoked both as a file and as a module).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.voice_pack.pack import (  # noqa: E402
    VOICE_PACK_FORMAT_VERSION,
    VALID_TIERS,
    VoicePackMeta,
    load_pack,
)

import re  # noqa: E402


_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9_-]+")
_SLUG_COLLAPSE_RE = re.compile(r"_+")


def _slugify(name: str) -> str:
    """Turn a display name into a filesystem-safe folder slug.

    ASCII-folds with NFKD normalisation (non-ASCII bytes are dropped, not
    transliterated), lowercases, replaces any run of non-``[a-z0-9_-]``
    characters with a single underscore, collapses repeats, and strips
    leading/trailing underscores. Falls back to ``'voice-pack'`` if the
    result is empty.
    """

    folded = unicodedata.normalize("NFKD", name)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.strip().lower()
    cleaned = _SLUG_CLEAN_RE.sub("_", lowered)
    collapsed = _SLUG_COLLAPSE_RE.sub("_", cleaned).strip("_")
    return collapsed or "voice-pack"


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with ``+00:00`` offset."""

    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def build_meta(
    *,
    name: str,
    language: str,
    tier: str,
    tier_reason: str,
    total_source_minutes: float,
    emotion_coverage: dict[str, int] | None = None,
    base_model: str = "chatterbox-multilingual",
    notes: str = "",
    now_iso: str | None = None,
) -> VoicePackMeta:
    """Build a :class:`VoicePackMeta` with sensible defaults.

    ``created_at`` falls back to the current UTC time in ISO-8601 form when
    ``now_iso`` is not supplied. ``format_version`` is always the current
    :data:`VOICE_PACK_FORMAT_VERSION`.
    """

    created_at = now_iso if now_iso is not None else _utc_now_iso()
    return VoicePackMeta(
        name=name,
        language=language,
        tier=tier,
        tier_reason=tier_reason,
        total_source_minutes=float(total_source_minutes),
        emotion_coverage=dict(emotion_coverage or {}),
        base_model=base_model,
        format_version=VOICE_PACK_FORMAT_VERSION,
        created_at=created_at,
        notes=notes,
    )


def _require_file(path: Path, label: str) -> None:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")


def _resolve_adapter_path(raw: str | Path) -> Path:
    """Accept either a single-file adapter or a PEFT save directory.

    ``peft.PeftModel.save_pretrained`` produces a *directory* containing
    ``adapter_model.safetensors`` + ``adapter_config.json``. Users
    (rightly) point ``--adapter`` at that directory, and used to get a
    cryptic ``FileNotFoundError``. Now we accept both forms: if a
    directory is passed, we pick ``adapter_model.safetensors`` (or
    fall back to ``adapter_model.bin``) from inside it.
    """

    path = Path(raw)
    if path.is_dir():
        for candidate in ("adapter_model.safetensors", "adapter_model.bin"):
            inner = path / candidate
            if inner.is_file():
                return inner
        raise FileNotFoundError(
            f"adapter directory {path} does not contain "
            f"adapter_model.safetensors or adapter_model.bin"
        )
    return path


def package(
    *,
    out_dir: str | Path,
    name: str,
    language: str,
    tier: str,
    tier_reason: str,
    total_source_minutes: float,
    sample_path: str | Path,
    adapter_path: str | Path | None = None,
    reference_path: str | Path | None = None,
    emotion_coverage: dict[str, int] | None = None,
    base_model: str = "chatterbox-multilingual",
    notes: str = "",
    slug: str | None = None,
    overwrite: bool = False,
    now_iso: str | None = None,
) -> Path:
    """Produce a voice pack folder at ``out_dir/<slug>`` and return its path.

    Raises
    ------
    ValueError
        If ``tier`` is not one of :data:`VALID_TIERS`, or if the tier-specific
        asset is missing (``adapter_path`` for LoRA tiers; ``reference_path``
        for ``few_shot``).
    FileNotFoundError
        If any supplied input file does not exist.
    FileExistsError
        If the destination folder already exists and ``overwrite`` is False.
    """

    if tier not in VALID_TIERS:
        raise ValueError(
            f"unknown tier {tier!r}; expected one of {', '.join(VALID_TIERS)}"
        )

    if tier in ("full_lora", "reduced_lora"):
        if adapter_path is None:
            raise ValueError(
                f"tier {tier!r} requires --adapter (adapter_path) to be supplied"
            )
    elif tier == "few_shot":
        if reference_path is None:
            raise ValueError(
                "tier 'few_shot' requires --reference (reference_path) to be supplied"
            )

    sample = Path(sample_path)
    _require_file(sample, "sample")

    adapter: Path | None = None
    if adapter_path is not None:
        adapter = _resolve_adapter_path(adapter_path)
        _require_file(adapter, "adapter")

    reference: Path | None = None
    if reference_path is not None:
        reference = Path(reference_path)
        _require_file(reference, "reference")

    resolved_slug = slug if slug else _slugify(name)
    out_root = Path(out_dir)
    destination = out_root / resolved_slug

    if destination.exists():
        if not overwrite:
            raise FileExistsError(
                f"voice pack destination already exists: {destination}"
            )
        shutil.rmtree(destination)

    out_root.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=False)

    meta = build_meta(
        name=name,
        language=language,
        tier=tier,
        tier_reason=tier_reason,
        total_source_minutes=total_source_minutes,
        emotion_coverage=emotion_coverage,
        base_model=base_model,
        notes=notes,
        now_iso=now_iso,
    )

    shutil.copy2(sample, destination / "sample.wav")
    if tier in ("full_lora", "reduced_lora") and adapter is not None:
        shutil.copy2(adapter, destination / "adapter.pt")
        # If the adapter originates from a peft-native save directory
        # (or sits next to one), copy ``adapter_config.json`` across so
        # inference can reconstruct the wrapper without hard-coding the
        # LoRA hyperparameters. ``_resolve_adapter_path`` returns the
        # weights file even when the user pointed at a directory, so
        # check both the adapter file's parent (typical peft layout) and
        # the originally-supplied path (if it was a directory).
        source_candidates: list[Path] = [adapter.parent]
        raw_adapter = Path(adapter_path) if adapter_path is not None else None
        if raw_adapter is not None and raw_adapter.is_dir():
            source_candidates.append(raw_adapter)
        for candidate_dir in source_candidates:
            cfg_src = candidate_dir / "adapter_config.json"
            if cfg_src.is_file():
                shutil.copy2(cfg_src, destination / "adapter_config.json")
                break
    if tier == "few_shot" and reference is not None:
        shutil.copy2(reference, destination / "reference.wav")

    meta_yaml = yaml.safe_dump(
        meta.to_dict(), sort_keys=False, allow_unicode=True
    )
    (destination / "meta.yaml").write_text(meta_yaml, encoding="utf-8")

    return destination


def _parse_emotion_coverage(raw: str | None) -> dict[str, int] | None:
    """Parse a ``k=v,k=v`` string into ``dict[str, int]``.

    Empty / ``None`` input returns ``None``. Raises :class:`ValueError`
    naming the offending token when a piece does not look like ``key=int``.
    """

    if not raw:
        return None
    result: dict[str, int] = {}
    for token in raw.split(","):
        piece = token.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise ValueError(
                f"emotion-coverage token {piece!r} is not in key=value form"
            )
        key, _, value = piece.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(
                f"emotion-coverage token {piece!r} has empty key"
            )
        try:
            result[key] = int(value)
        except ValueError as exc:
            raise ValueError(
                f"emotion-coverage token {piece!r} has non-integer value"
            ) from exc
    return result


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Package a trained voice into an installable voice pack.",
    )
    parser.add_argument("--out", required=True, help="target root directory")
    parser.add_argument("--name", required=True, help="display name")
    parser.add_argument("--language", default="en", help="language code")
    parser.add_argument(
        "--tier",
        required=True,
        choices=list(VALID_TIERS),
        help="voice pack quality tier",
    )
    parser.add_argument(
        "--tier-reason", required=True, help="why this tier was chosen"
    )
    parser.add_argument(
        "--total-source-minutes",
        required=True,
        type=float,
        help="total minutes of source audio used",
    )
    parser.add_argument("--sample", required=True, help="path to sample.wav source")
    parser.add_argument(
        "--adapter",
        default=None,
        help=(
            "path to LoRA adapter weights (required for full_lora / reduced_lora). "
            "Accepts either the .safetensors/.bin file or the PEFT save "
            "directory that contains it."
        ),
    )
    parser.add_argument(
        "--reference",
        default=None,
        help="path to reference.wav (required for few_shot)",
    )
    parser.add_argument(
        "--emotion-coverage",
        default=None,
        help="comma-separated key=int tokens, e.g. 'neutral=120,angry=40'",
    )
    parser.add_argument(
        "--base-model",
        default="chatterbox-multilingual",
        help="base TTS model identifier",
    )
    parser.add_argument("--notes", default="", help="free-form notes")
    parser.add_argument(
        "--slug",
        default=None,
        help="explicit slug for destination folder (else derived from name)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing destination folder",
    )
    return parser


def main(argv: Sequence[str] | list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, 1 on handled failure."""

    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        emotion_coverage = _parse_emotion_coverage(args.emotion_coverage)
        result = package(
            out_dir=args.out,
            name=args.name,
            language=args.language,
            tier=args.tier,
            tier_reason=args.tier_reason,
            total_source_minutes=args.total_source_minutes,
            sample_path=args.sample,
            adapter_path=args.adapter,
            reference_path=args.reference,
            emotion_coverage=emotion_coverage,
            base_model=args.base_model,
            notes=args.notes,
            slug=args.slug,
            overwrite=args.overwrite,
        )
    except (ValueError, FileNotFoundError, FileExistsError, OSError) as exc:
        print(f"voice_pack_package: error: {exc}", file=sys.stderr)
        return 1

    print(str(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
