"""Voice-pack LoRA training scaffold.

This module is the training entry point for the voice-pack pipeline. It
fine-tunes a LoRA adapter on top of the base multilingual Chatterbox model
for a single speaker, using a :class:`DatasetManifest` produced by the
earlier dataset-export stage.

What this file does:

* Defines :class:`TrainConfig`, the single source of truth for training
  hyperparameters.
* Parses a CLI, applies tier-appropriate defaults via
  :func:`apply_tier_policy` (full-LoRA vs reduced-LoRA), and persists the
  effective config to the run directory so a training run is reproducible.
* Validates the dataset manifest and stages everything a GPU host needs:
  ``config.json``, ``manifest_snapshot.json``, ``run_command.txt``.
* Supports ``--dry-run`` so the scaffold can be exercised without torch.
* Runs the LoRA training loop (:func:`_run_training` → :func:`_run_training_impl`)
  on a CUDA GPU host with ``torch``, ``peft``, and the ``chatterbox`` package
  available. On hosts that lack these, :func:`_run_training` raises
  :class:`NotImplementedError` with a clear message — the import guard is
  intentional so the test suite and CI stay hermetic.

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
    """Train a LoRA adapter on top of the multilingual Chatterbox model.

    This is an import guard. If the GPU-only stack (``torch``, ``peft``,
    ``chatterbox``) can't be loaded, or if CUDA isn't available, we raise
    :class:`NotImplementedError` with a clear message so CI and test
    environments without a GPU still pass. On a GPU host with the stack
    installed, control passes to :func:`_run_training_impl` which runs the
    actual training loop.

    The training loop:

    1. Loads the base Chatterbox multilingual model on CUDA.
    2. Freezes all weights, then wraps ``engine.t3.tfmr`` (the Llama
       transformer backbone) with PEFT LoRA on all four attention
       projections (``q/k/v/o_proj``).
    3. Builds a per-clip dataset: resamples each WAV to 24 kHz / 16 kHz as
       needed, tokenises text via ``engine.tokenizer.text_to_tokens``,
       encodes speech tokens via ``engine.s3gen.tokenizer``, and computes a
       speaker embedding via ``engine.ve.embeds_from_wavs``.
    4. Runs AdamW + cosine LR schedule with warmup, gradient accumulation,
       fp16/bf16 mixed precision, and early stopping on the training loss.
    5. Saves the best adapter to ``out_dir / 'adapter'`` via
       ``peft.PeftModel.save_pretrained`` and writes a step-level metrics
       log to ``out_dir / 'training.log'``.
    """
    # Import guard: these imports must all succeed, and CUDA must be
    # available, before we'll dispatch to the actual training loop.
    try:
        import torch  # type: ignore[import-not-found]  # noqa: F401
        import peft  # type: ignore[import-not-found]  # noqa: F401
        import torchaudio  # type: ignore[import-not-found]  # noqa: F401
        import chatterbox  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as exc:
        raise NotImplementedError(
            "LoRA training requires torch + peft + torchaudio + chatterbox "
            "on a CUDA GPU host. Missing import: "
            f"{exc}. Run directory already prepared at {config.out_dir}."
        ) from exc

    if not torch.cuda.is_available():
        raise NotImplementedError(
            "LoRA training requires a CUDA GPU. No CUDA device detected. "
            f"Run directory already prepared at {config.out_dir}."
        )

    _run_training_impl(config, manifest)


# Emotion → `exaggeration` (emotion_adv) mapping. Conservative defaults —
# keep the training signal close to the base model's distribution so the
# adapter learns timbre and accent, not exaggerated prosody. These match
# the inference-time defaults used elsewhere in the pipeline.
_EMOTION_TO_EXAGGERATION: dict[str, float] = {
    "neutral": 0.5,
    "happy": 0.65,
    "sad": 0.4,
    "angry": 0.7,
    "unknown": 0.5,
}


def _run_training_impl(config: TrainConfig, manifest: DatasetManifest) -> None:
    """Actual LoRA training loop. Called from :func:`_run_training` only
    after import + CUDA availability checks pass.

    This function is not covered by unit tests — it only runs on GPU hosts
    with the full TTS stack installed. Validation is done end-to-end on a
    real voice-pack dataset.
    """
    import math
    import random
    import time

    import numpy as np  # type: ignore[import-not-found]
    import torch  # type: ignore[import-not-found]
    import torchaudio  # type: ignore[import-not-found]
    from peft import LoraConfig, get_peft_model  # type: ignore[import-not-found]
    from torch.optim import AdamW  # type: ignore[import-not-found]
    from torch.optim.lr_scheduler import LambdaLR  # type: ignore[import-not-found]
    from torch.utils.data import DataLoader, Dataset  # type: ignore[import-not-found]

    from chatterbox.models.t3 import T3  # type: ignore[import-not-found]
    from chatterbox.models.t3.modules.cond_enc import T3Cond  # type: ignore[import-not-found]
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # type: ignore[import-not-found]

    # --- Patch chatterbox T3.loss shape bug ----------------------------
    # The upstream `T3.loss` passes logits shaped [B, L, V] straight to
    # `F.cross_entropy`, which expects `[B, V, L]` (or flattened). That
    # raises "Expected target size [B, V], got [B, L]" at step 0. Fix:
    # transpose logits to put the class dim second. No behaviour change
    # beyond making the loss actually compute.
    #
    # Guarded by a source sniff so that if chatterbox ships a real fix
    # upstream, our patch becomes a no-op instead of silently shadowing
    # a corrected implementation. Markers we look for: any `.transpose(`
    # / `.permute(` on the logits, which is how a proper fix would read.
    import inspect  # type: ignore[import-not-found]
    import torch.nn.functional as F  # type: ignore[import-not-found]

    try:
        _t3_loss_src = inspect.getsource(T3.loss)
    except (OSError, TypeError):
        _t3_loss_src = ""
    _already_fixed_upstream = (
        ".transpose(" in _t3_loss_src or ".permute(" in _t3_loss_src
    )

    def _fixed_t3_loss(
        self: T3,
        *,
        t3_cond,
        text_tokens,
        text_token_lens,
        speech_tokens,
        speech_token_lens,
    ):
        len_text = text_tokens.size(1)
        len_speech = speech_tokens.size(1)
        assert len_text == int(text_token_lens.max())
        assert len_speech == int(speech_token_lens.max())

        out = self.forward(
            t3_cond=t3_cond,
            text_tokens=text_tokens,
            text_token_lens=text_token_lens,
            speech_tokens=speech_tokens,
            speech_token_lens=speech_token_lens,
            training=True,
        )

        IGNORE_ID = -100
        dev = out.text_logits.device
        mask_text = (
            torch.arange(len_text, device=dev)[None] >= text_token_lens[:, None]
        )
        mask_speech = (
            torch.arange(len_speech, device=dev)[None] >= speech_token_lens[:, None]
        )
        masked_text = text_tokens.masked_fill(mask_text, IGNORE_ID)
        masked_speech = speech_tokens.masked_fill(mask_speech, IGNORE_ID)

        # transpose [B, L, V] -> [B, V, L] so cross_entropy picks the
        # right class dim; masked labels stay [B, L].
        loss_text = F.cross_entropy(
            out.text_logits.transpose(1, 2),
            masked_text,
            ignore_index=IGNORE_ID,
        )
        loss_speech = F.cross_entropy(
            out.speech_logits.transpose(1, 2),
            masked_speech,
            ignore_index=IGNORE_ID,
        )
        return loss_text, loss_speech

    if _already_fixed_upstream:
        print(
            "[voice_pack_train] detected fixed T3.loss upstream — "
            "skipping local monkey-patch.",
            flush=True,
        )
    else:
        T3.loss = _fixed_t3_loss  # type: ignore[assignment]

    # --- Determinism (best-effort; torch non-determinism on GPU is real) -
    torch.manual_seed(config.seed)
    random.seed(config.seed)
    np.random.seed(config.seed)

    device = torch.device("cuda")

    # --- Load base model ------------------------------------------------
    engine = ChatterboxMultilingualTTS.from_pretrained(device=device)

    # Freeze everything; LoRA wrapper re-enables its own params below.
    for p in engine.t3.parameters():
        p.requires_grad = False

    lora_cfg = LoraConfig(
        r=config.lora_rank,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        bias="none",
    )
    engine.t3.tfmr = get_peft_model(engine.t3.tfmr, lora_cfg)
    # `get_peft_model` re-registers LoRA params with requires_grad=True,
    # but to be safe we explicitly mark them trainable and collect them.
    trainable_params: list[torch.nn.Parameter] = []
    for name, param in engine.t3.named_parameters():
        if "lora_" in name:
            param.requires_grad = True
            trainable_params.append(param)
    if not trainable_params:
        raise RuntimeError(
            "No LoRA parameters found after wrapping. Check that "
            "target_modules match the model's module names."
        )

    # --- Dataset + loader -----------------------------------------------
    sot = int(engine.t3.hp.start_text_token)
    eot = int(engine.t3.hp.stop_text_token)
    start_speech = int(engine.t3.hp.start_speech_token)
    stop_speech = int(engine.t3.hp.stop_speech_token)

    class _VoicePackDataset(Dataset):
        """Loads one DatasetClip into the tensors T3.loss expects.

        We intentionally keep this CPU-only and let the DataLoader's main
        thread do the work. The Chatterbox submodels (ve, s3gen.tokenizer)
        are nn.Modules on CUDA; we run them under inference_mode here, not
        as part of the forward graph.
        """

        def __init__(self, m: DatasetManifest) -> None:
            self.manifest = m

        def __len__(self) -> int:
            return len(self.manifest.clips)

        def __getitem__(self, idx: int) -> dict:
            clip = self.manifest.clips[idx]
            wav_path = self.manifest.root_dir / clip.path
            wav, sr = torchaudio.load(str(wav_path))
            if wav.shape[0] > 1:
                wav = wav.mean(dim=0, keepdim=True)
            wav_16k_np = (
                torchaudio.functional.resample(wav, sr, 16000)
                .squeeze(0)
                .numpy()
                .astype(np.float32)
            )

            # Text tokens. language_id="en" for the current English
            # audiobook use case; when the pipeline grows multi-lingual
            # callers, the manifest will carry a language field and we'll
            # plumb it through.
            tt = engine.tokenizer.text_to_tokens(
                clip.text, language_id="en"
            ).squeeze(0).long()
            text_tokens = torch.cat(
                [
                    torch.tensor([sot], dtype=torch.long),
                    tt,
                    torch.tensor([eot], dtype=torch.long),
                ]
            )

            # Speech tokens from S3 tokenizer (16 kHz input).
            with torch.inference_mode():
                st_batch, _ = engine.s3gen.tokenizer([wav_16k_np])
            st = st_batch.squeeze(0).detach().cpu().long()
            speech_tokens = torch.cat(
                [
                    torch.tensor([start_speech], dtype=torch.long),
                    st,
                    torch.tensor([stop_speech], dtype=torch.long),
                ]
            )

            # Speaker embedding — numpy (B, 256) → (256,) mean.
            with torch.inference_mode():
                ve_embed_np = engine.ve.embeds_from_wavs(
                    [wav_16k_np], sample_rate=16000
                )
            speaker_emb = torch.from_numpy(np.asarray(ve_embed_np)).float()
            if speaker_emb.ndim == 2:
                speaker_emb = speaker_emb.mean(dim=0)

            emotion_adv = torch.tensor(
                [_EMOTION_TO_EXAGGERATION.get(clip.emotion, 0.5)],
                dtype=torch.float32,
            )

            return {
                "text_tokens": text_tokens,
                "text_token_len": torch.tensor(
                    text_tokens.shape[0], dtype=torch.long
                ),
                "speech_tokens": speech_tokens,
                "speech_token_len": torch.tensor(
                    speech_tokens.shape[0], dtype=torch.long
                ),
                "speaker_emb": speaker_emb,
                "emotion_adv": emotion_adv,
            }

    def _collate(batch: list[dict]) -> dict:
        text_lens = torch.stack([b["text_token_len"] for b in batch])
        speech_lens = torch.stack([b["speech_token_len"] for b in batch])
        max_text = int(text_lens.max().item())
        max_speech = int(speech_lens.max().item())

        text = torch.zeros(len(batch), max_text, dtype=torch.long)
        speech = torch.zeros(len(batch), max_speech, dtype=torch.long)
        for i, b in enumerate(batch):
            tlen = int(b["text_token_len"].item())
            slen = int(b["speech_token_len"].item())
            text[i, :tlen] = b["text_tokens"]
            speech[i, :slen] = b["speech_tokens"]

        speaker_emb = torch.stack([b["speaker_emb"] for b in batch])
        # T3Cond expects emotion_adv shape (B, 1, 1).
        emotion_adv = torch.stack(
            [b["emotion_adv"] for b in batch]
        ).unsqueeze(-1)

        return {
            "text_tokens": text,
            "text_token_lens": text_lens,
            "speech_tokens": speech,
            "speech_token_lens": speech_lens,
            "speaker_emb": speaker_emb,
            "emotion_adv": emotion_adv,
        }

    dataset = _VoicePackDataset(manifest)
    # num_workers=0: DataLoader workers + CUDA submodels don't mix well on
    # Windows, and the dataset already does heavy lifting (resample +
    # s3_tokenizer) on the main thread — sequential is fine here.
    loader = DataLoader(
        dataset,
        batch_size=config.batch_size,
        shuffle=True,
        collate_fn=_collate,
        num_workers=0,
        drop_last=False,
    )

    # --- Optimizer + scheduler ------------------------------------------
    optimizer = AdamW(
        trainable_params,
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    batches_per_epoch = max(1, len(loader))
    steps_per_epoch = max(
        1, batches_per_epoch // max(1, config.grad_accum_steps)
    )
    total_steps = config.max_steps or (steps_per_epoch * config.epochs)
    warmup_steps = max(1, int(total_steps * config.warmup_ratio))

    def _lr_lambda(step: int) -> float:
        if step < warmup_steps:
            return float(step) / float(max(1, warmup_steps))
        progress = (step - warmup_steps) / max(1, total_steps - warmup_steps)
        progress = min(1.0, max(0.0, progress))
        return 0.5 * (1.0 + math.cos(math.pi * progress))

    scheduler = LambdaLR(optimizer, _lr_lambda)

    # --- Mixed precision ------------------------------------------------
    use_fp16 = config.mixed_precision == "fp16"
    use_bf16 = config.mixed_precision == "bf16"
    autocast_dtype = (
        torch.float16 if use_fp16 else torch.bfloat16 if use_bf16 else torch.float32
    )
    scaler = torch.amp.GradScaler("cuda", enabled=use_fp16)

    # --- Output staging -------------------------------------------------
    adapter_dir = config.out_dir / "adapter"
    adapter_dir.mkdir(parents=True, exist_ok=True)
    log_path = config.out_dir / "training.log"

    best_loss = float("inf")
    no_improve_evals = 0
    global_step = 0
    start_wall = time.time()

    with log_path.open("w", encoding="utf-8") as logf:
        logf.write(
            "# voice_pack_train run log\n"
            f"# speaker={manifest.speaker} "
            f"clips={len(manifest.clips)} "
            f"total_seconds={manifest.total_seconds:.1f}\n"
            f"# lora_rank={config.lora_rank} lora_alpha={config.lora_alpha} "
            f"lora_dropout={config.lora_dropout}\n"
            f"# total_steps={total_steps} warmup_steps={warmup_steps} "
            f"batch_size={config.batch_size} "
            f"grad_accum={config.grad_accum_steps} "
            f"mixed_precision={config.mixed_precision}\n"
        )
        logf.flush()

        engine.t3.train()
        should_stop = False

        for epoch in range(config.epochs):
            if should_stop:
                break
            optimizer.zero_grad(set_to_none=True)
            micro_step_in_accum = 0

            for batch in loader:
                # Move tensors to device; reconstruct T3Cond on device.
                text_tokens = batch["text_tokens"].to(device)
                text_token_lens = batch["text_token_lens"].to(device)
                speech_tokens = batch["speech_tokens"].to(device)
                speech_token_lens = batch["speech_token_lens"].to(device)
                speaker_emb = batch["speaker_emb"].to(device)
                emotion_adv = batch["emotion_adv"].to(device).unsqueeze(-1)

                t3_cond = T3Cond(
                    speaker_emb=speaker_emb,
                    cond_prompt_speech_tokens=None,
                    emotion_adv=emotion_adv,
                )

                with torch.amp.autocast(
                    "cuda",
                    dtype=autocast_dtype,
                    enabled=config.mixed_precision != "no",
                ):
                    loss_text, loss_speech = engine.t3.loss(
                        t3_cond=t3_cond,
                        text_tokens=text_tokens,
                        text_token_lens=text_token_lens,
                        speech_tokens=speech_tokens,
                        speech_token_lens=speech_token_lens,
                    )
                    loss = (loss_text + loss_speech) / config.grad_accum_steps

                if use_fp16:
                    scaler.scale(loss).backward()
                else:
                    loss.backward()

                micro_step_in_accum += 1
                if micro_step_in_accum < config.grad_accum_steps:
                    continue

                # Optimizer step
                if use_fp16:
                    scaler.step(optimizer)
                    scaler.update()
                else:
                    optimizer.step()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)
                micro_step_in_accum = 0
                global_step += 1

                reported_loss = float(
                    loss.item() * config.grad_accum_steps
                )
                lr_now = scheduler.get_last_lr()[0]
                logf.write(
                    f"step={global_step} epoch={epoch} "
                    f"loss={reported_loss:.4f} "
                    f"text={float(loss_text.item()):.4f} "
                    f"speech={float(loss_speech.item()):.4f} "
                    f"lr={lr_now:.3e}\n"
                )
                logf.flush()

                # Checkpoint cadence: save on improvement (best-so-far).
                # config.save_every_n_steps is used only for the
                # improvement eval cadence below.
                if global_step % max(1, config.eval_every_n_steps) == 0:
                    if reported_loss < best_loss:
                        best_loss = reported_loss
                        no_improve_evals = 0
                        engine.t3.tfmr.save_pretrained(str(adapter_dir))
                        logf.write(
                            f"# checkpoint step={global_step} "
                            f"best_loss={best_loss:.4f}\n"
                        )
                        logf.flush()
                    else:
                        no_improve_evals += 1
                        if (
                            config.early_stopping_patience is not None
                            and no_improve_evals
                            >= config.early_stopping_patience
                        ):
                            logf.write(
                                f"# early_stop step={global_step} "
                                f"patience={config.early_stopping_patience}\n"
                            )
                            should_stop = True
                            break

                if config.max_steps and global_step >= config.max_steps:
                    should_stop = True
                    break

        # Always save the final state — if the best-so-far didn't update
        # in the final eval window we still want a usable adapter on disk.
        engine.t3.tfmr.save_pretrained(str(adapter_dir))
        wall = time.time() - start_wall
        logf.write(
            f"# done steps={global_step} "
            f"best_loss={best_loss:.4f} "
            f"wall_seconds={wall:.1f}\n"
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
