"""Voice-pack LoRA training scaffold.

This module is the training entry point for the voice-pack pipeline. It
fine-tunes a LoRA adapter on top of the base multilingual Chatterbox model
for a single speaker, using a :class:`DatasetManifest` produced by the
earlier dataset-export stage.

What this file does *today*:

* Defines :class:`TrainConfig`, the single source of truth for training
  hyperparameters.
* Parses a CLI, applies tier-appropriate defaults via
  :func:`apply_tier_policy` (full-LoRA vs reduced-LoRA), and persists the
  effective config to the run directory so a training run is reproducible.
* Validates the dataset manifest and stages everything a GPU host needs:
  ``config.json``, ``manifest_snapshot.json``, ``run_command.txt``.
* Supports ``--dry-run`` so the scaffold can be exercised without torch.

What this file *does not yet* do:

* The inner training loop (:func:`_run_training`) is a seam. It raises
  :class:`NotImplementedError` with a checklist pointing at the GPU-host
  commit that will flesh it out. The actual loop needs ``torch``, ``peft``,
  the Chatterbox multilingual weights, and a GPU — none of which are
  available in the test environment.

Quality-tier policy (see :func:`apply_tier_policy`):

* ``full_lora`` (>= 30 min)  — full LoRA rank, 3 epochs, standard patience.
* ``reduced_lora`` (10-30 min) — reduced rank/alpha, 2 epochs, higher
  dropout, tighter early-stopping patience. Flagged "experimental" in the
  GUI.
* ``few_shot`` / ``skip`` — not enough data to fine-tune; these tiers must
  use the existing few-shot reference-clip path instead. This CLI refuses
  them loudly rather than silently degrading quality.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Make ``src/`` importable so we can reuse the shared voice_pack types.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.voice_pack.types import (  # noqa: E402
    DatasetClip,
    DatasetManifest,
    classify_quality_tier,
)


@dataclass
class TrainConfig:
    """All hyperparameters for one LoRA training run.

    Instances are serialised to ``config.json`` in the run directory via
    :meth:`to_dict` so a training run can be reproduced byte-for-byte from
    the saved config alone.
    """

    manifest_path: Path
    out_dir: Path
    base_model: str = "chatterbox-multilingual"
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    learning_rate: float = 1e-4
    batch_size: int = 4
    grad_accum_steps: int = 4
    epochs: int = 3
    max_steps: int | None = None
    warmup_ratio: float = 0.05
    weight_decay: float = 0.01
    mixed_precision: str = "fp16"  # "fp16" | "bf16" | "no"
    early_stopping_patience: int | None = 3
    seed: int = 42
    save_every_n_steps: int = 500
    eval_every_n_steps: int = 500
    reduced_mode: bool = False  # True for 10-30 min tier

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict (paths are stringified)."""
        data = asdict(self)
        data["manifest_path"] = str(self.manifest_path)
        data["out_dir"] = str(self.out_dir)
        return data


def apply_tier_policy(config: TrainConfig, total_seconds: float) -> TrainConfig:
    """Return a new :class:`TrainConfig` with tier-appropriate defaults.

    The policy is:

    * ``full_lora`` (>= 30 min): keep the config as-is. If ``lora_rank`` is
      still at the scaffold default of 16, bump it to 32 — at full-LoRA
      data volumes the higher rank earns its keep.
    * ``reduced_lora`` (10-30 min): clamp to a conservative configuration —
      rank 8, alpha 16, dropout 0.1, 2 epochs, early-stopping patience 2,
      ``reduced_mode=True``. This is the "experimental quality" tier.
    * ``few_shot`` / ``skip``: raise :class:`ValueError`. These tiers lack
      the data volume to fine-tune productively and must route through the
      few-shot reference-clip path instead.
    """
    tier = classify_quality_tier(total_seconds)

    # Shallow copy via asdict+reconstruction so we don't mutate the caller.
    kwargs = asdict(config)
    kwargs["manifest_path"] = config.manifest_path
    kwargs["out_dir"] = config.out_dir

    if tier == "full_lora":
        if kwargs["lora_rank"] == 16:
            kwargs["lora_rank"] = 32
        kwargs["reduced_mode"] = False
        return TrainConfig(**kwargs)

    if tier == "reduced_lora":
        kwargs["lora_rank"] = 8
        kwargs["lora_alpha"] = 16
        kwargs["lora_dropout"] = 0.1
        kwargs["epochs"] = 2
        kwargs["early_stopping_patience"] = 2
        kwargs["reduced_mode"] = True
        return TrainConfig(**kwargs)

    # few_shot or skip: refuse.
    raise ValueError(
        f"Tier {tier} does not support LoRA training. "
        "Use few-shot ref-clip extraction instead."
    )


def load_manifest(manifest_path: str | Path) -> DatasetManifest:
    """Load a ``manifest.json`` produced by the dataset-export stage.

    Reconstructs :class:`DatasetClip` instances from the raw dict so
    downstream code sees proper typed objects instead of bare dicts.
    """
    path = Path(manifest_path)
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    clips = [
        DatasetClip(
            path=c["path"],
            text=c["text"],
            emotion=c["emotion"],
            speaker=c["speaker"],
            duration=float(c["duration"]),
        )
        for c in data.get("clips", [])
    ]
    return DatasetManifest(
        speaker=data["speaker"],
        root_dir=Path(data["root_dir"]),
        clips=clips,
        total_seconds=float(data.get("total_seconds", 0.0)),
        emotion_counts=dict(data.get("emotion_counts", {})),
        sample_rate_hz=int(data.get("sample_rate_hz", 24000)),
    )


def prepare_training_run(
    config: TrainConfig,
    *,
    manifest: DatasetManifest | None = None,
) -> Path:
    """Validate, stage, and persist a reproducible training run directory.

    Writes:

    * ``config.json``            — effective :class:`TrainConfig`.
    * ``manifest_snapshot.json`` — copy of the dataset manifest so the run
                                    directory is self-contained.
    * ``run_command.txt``        — exact ``python scripts/voice_pack_train.py``
                                    invocation to reproduce the run.

    Returns the out_dir path.
    """
    # Load the manifest if the caller didn't pass one in.
    if manifest is None:
        if not config.manifest_path.exists():
            raise FileNotFoundError(
                f"Manifest not found: {config.manifest_path}"
            )
        manifest = load_manifest(config.manifest_path)

    out_dir = Path(config.out_dir)
    parent = out_dir.parent
    if not parent.exists():
        # The parent's parent must exist or makedirs would be a mistake.
        if not parent.parent.exists():
            raise FileNotFoundError(
                f"Cannot create out_dir {out_dir}: "
                f"grandparent {parent.parent} does not exist."
            )
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "config.json").write_text(
        json.dumps(config.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )
    (out_dir / "manifest_snapshot.json").write_text(
        json.dumps(manifest.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )
    (out_dir / "run_command.txt").write_text(
        _reconstruct_run_command(config),
        encoding="utf-8",
    )
    return out_dir


def _reconstruct_run_command(config: TrainConfig) -> str:
    """Build the exact ``python scripts/voice_pack_train.py ...`` command.

    Used for reproducibility — drop this into a GPU host's shell and the
    same run is retriggered.
    """
    parts: list[str] = [
        "python",
        "scripts/voice_pack_train.py",
        "--manifest",
        str(config.manifest_path),
        "--out",
        str(config.out_dir),
        "--base-model",
        config.base_model,
        "--lora-rank",
        str(config.lora_rank),
        "--lora-alpha",
        str(config.lora_alpha),
        "--lora-dropout",
        str(config.lora_dropout),
        "--lr",
        str(config.learning_rate),
        "--batch-size",
        str(config.batch_size),
        "--grad-accum",
        str(config.grad_accum_steps),
        "--epochs",
        str(config.epochs),
        "--warmup-ratio",
        str(config.warmup_ratio),
        "--weight-decay",
        str(config.weight_decay),
        "--mixed-precision",
        config.mixed_precision,
        "--seed",
        str(config.seed),
        "--save-every-n-steps",
        str(config.save_every_n_steps),
        "--eval-every-n-steps",
        str(config.eval_every_n_steps),
    ]
    if config.max_steps is not None:
        parts += ["--max-steps", str(config.max_steps)]
    if config.early_stopping_patience is not None:
        parts += [
            "--early-stopping-patience",
            str(config.early_stopping_patience),
        ]
    return " ".join(parts) + "\n"


def _run_training(config: TrainConfig, manifest: DatasetManifest) -> None:
    """Inner training loop — **not implemented in this commit**.

    Actual training requires ``torch``, ``peft``, the Chatterbox
    multilingual checkpoint, and a GPU. None of those belong in the test
    environment, so this function is a clearly marked seam.

    Implementation checklist for the GPU-host commit:

    1. Load the base Chatterbox multilingual model via
       ``chatterbox_tts.load(...)``.
    2. Wrap it with ``peft.LoraConfig`` + ``peft.get_peft_model`` targeting
       the attention projection modules (``q``, ``k``, ``v``, ``o``).
    3. Build a ``torch.utils.data.Dataset`` from ``manifest`` — load each
       wav at ``manifest.sample_rate_hz`` and tokenise ``clip.text``.
    4. Standard training loop: AdamW, cosine LR schedule with warmup, grad
       accumulation per ``config.grad_accum_steps``, mixed precision per
       ``config.mixed_precision``, early stopping per
       ``config.early_stopping_patience``.
    5. Save the adapter via
       ``peft.PeftModel.save_pretrained(out_dir / 'adapter')``.
    6. Log metrics to ``out_dir / 'training.log'``.
    """
    raise NotImplementedError(
        "LoRA training loop is not implemented in this commit. "
        "It requires torch + peft + Chatterbox weights on a GPU host. "
        "See the implementation checklist in _run_training's docstring "
        "and run this script on a GPU host once that commit lands. "
        f"Run directory already prepared at {config.out_dir}."
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice_pack_train",
        description=(
            "Fine-tune a LoRA adapter on top of the multilingual Chatterbox "
            "base model for a single speaker. Reads a DatasetManifest JSON "
            "produced by the voice-pack dataset-export stage and stages a "
            "reproducible training run directory. Use --dry-run to validate "
            "inputs and produce the run directory without invoking torch."
        ),
    )
    parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="Path to the DatasetManifest JSON for one speaker.",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output directory for the training run (config + adapter).",
    )
    parser.add_argument(
        "--base-model",
        default="chatterbox-multilingual",
        help="Base TTS checkpoint identifier (default: chatterbox-multilingual).",
    )
    parser.add_argument(
        "--lora-rank", type=int, default=16, help="LoRA rank r (default: 16)."
    )
    parser.add_argument(
        "--lora-alpha", type=int, default=32, help="LoRA alpha (default: 32)."
    )
    parser.add_argument(
        "--lora-dropout",
        type=float,
        default=0.05,
        help="LoRA dropout probability (default: 0.05).",
    )
    parser.add_argument(
        "--lr",
        dest="learning_rate",
        type=float,
        default=1e-4,
        help="Peak learning rate (default: 1e-4).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=4,
        help="Per-device batch size (default: 4).",
    )
    parser.add_argument(
        "--grad-accum",
        dest="grad_accum_steps",
        type=int,
        default=4,
        help="Gradient accumulation steps (default: 4).",
    )
    parser.add_argument(
        "--epochs", type=int, default=3, help="Number of epochs (default: 3)."
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Hard cap on optimizer steps; overrides --epochs when set.",
    )
    parser.add_argument(
        "--warmup-ratio",
        type=float,
        default=0.05,
        help="LR warmup as a fraction of total steps (default: 0.05).",
    )
    parser.add_argument(
        "--weight-decay",
        type=float,
        default=0.01,
        help="AdamW weight decay (default: 0.01).",
    )
    parser.add_argument(
        "--mixed-precision",
        choices=["fp16", "bf16", "no"],
        default="fp16",
        help="Mixed-precision mode (default: fp16).",
    )
    parser.add_argument(
        "--early-stopping-patience",
        type=int,
        default=3,
        help="Eval rounds without improvement before stopping (default: 3).",
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed (default: 42)."
    )
    parser.add_argument(
        "--save-every-n-steps",
        type=int,
        default=500,
        help="Checkpoint cadence in optimizer steps (default: 500).",
    )
    parser.add_argument(
        "--eval-every-n-steps",
        type=int,
        default=500,
        help="Evaluation cadence in optimizer steps (default: 500).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate inputs and prepare the run directory, but skip the "
            "actual training loop. Useful for testing the scaffold."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print tier and final config to stdout.",
    )
    return parser


def _config_from_args(args: argparse.Namespace) -> TrainConfig:
    return TrainConfig(
        manifest_path=args.manifest,
        out_dir=args.out,
        base_model=args.base_model,
        lora_rank=args.lora_rank,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        learning_rate=args.learning_rate,
        batch_size=args.batch_size,
        grad_accum_steps=args.grad_accum_steps,
        epochs=args.epochs,
        max_steps=args.max_steps,
        warmup_ratio=args.warmup_ratio,
        weight_decay=args.weight_decay,
        mixed_precision=args.mixed_precision,
        early_stopping_patience=args.early_stopping_patience,
        seed=args.seed,
        save_every_n_steps=args.save_every_n_steps,
        eval_every_n_steps=args.eval_every_n_steps,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = _config_from_args(args)

        if not config.manifest_path.exists():
            print(
                f"error: manifest not found: {config.manifest_path}",
                file=sys.stderr,
            )
            return 1

        manifest = load_manifest(config.manifest_path)
        tier = classify_quality_tier(manifest.total_seconds)

        try:
            config = apply_tier_policy(config, manifest.total_seconds)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        if args.verbose:
            print(f"Quality tier: {tier}")
            print(f"Effective config: {json.dumps(config.to_dict(), indent=2, default=str)}")

        out_dir = prepare_training_run(config, manifest=manifest)

        if args.dry_run:
            cmd = _reconstruct_run_command(config).strip()
            print(
                f"Dry run complete. Run directory prepared at {out_dir}. "
                f"Training command: {cmd}"
            )
            return 0

        try:
            _run_training(config, manifest)
        except NotImplementedError as exc:
            print(
                "error: training loop not implemented in this commit.\n"
                f"  reason: {exc}\n"
                "  next step: run this command on a GPU host with torch + "
                "peft + Chatterbox weights installed once the training-loop "
                "commit lands.",
                file=sys.stderr,
            )
            return 1

        return 0

    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
