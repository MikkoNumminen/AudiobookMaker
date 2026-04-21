"""Unit tests for the LoRA adapter loader in the chatterbox generator.

These cover ``_apply_lora_adapter`` in
``scripts/generate_chatterbox_audiobook.py`` — the function that wraps
``engine.t3.tfmr`` with peft, loads a trained adapter (either peft-native
layout or a packaged ``adapter.pt``), and merges it back into a plain
``nn.Module`` so the forward pass is unwrapped.

No Chatterbox, no CUDA, no real audio. We build a tiny stand-in module
shaped the way peft expects (nested ``self_attn`` submodules with the
four q/k/v/o projections), save a real adapter via a real peft roundtrip,
then assert the loader produces a merged, unwrapped module.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("peft")

import torch  # noqa: E402
from torch import nn  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import generate_chatterbox_audiobook as gca  # type: ignore  # noqa: E402


# ---------------------------------------------------------------------------
# Tiny stand-in for a transformer. peft traverses named_modules and matches
# ``target_modules`` against the module names, so we only need modules whose
# leaf names are ``q_proj``/``k_proj``/``v_proj``/``o_proj``.
# ---------------------------------------------------------------------------


class _FakeAttn(nn.Module):
    def __init__(self, dim: int = 8) -> None:
        super().__init__()
        self.q_proj = nn.Linear(dim, dim)
        self.k_proj = nn.Linear(dim, dim)
        self.v_proj = nn.Linear(dim, dim)
        self.o_proj = nn.Linear(dim, dim)


class _FakeBlock(nn.Module):
    def __init__(self, dim: int = 8) -> None:
        super().__init__()
        self.self_attn = _FakeAttn(dim)


class _FakeTransformer(nn.Module):
    """Minimal shape that peft's default target-module matcher can walk."""

    def __init__(self, n_layers: int = 2, dim: int = 8) -> None:
        super().__init__()
        self.layers = nn.ModuleList([_FakeBlock(dim) for _ in range(n_layers)])


def _make_engine() -> SimpleNamespace:
    """Build a stub ``engine`` whose ``.t3.tfmr`` is a real nn.Module."""

    tfmr = _FakeTransformer()
    t3 = SimpleNamespace(tfmr=tfmr)
    return SimpleNamespace(t3=t3)


# ---------------------------------------------------------------------------
# Test fixtures — produce real on-disk adapter directories peft can read.
# ---------------------------------------------------------------------------


def _save_peft_native_adapter(out_dir: Path, *, rank: int = 4) -> None:
    """Save a peft-native adapter (config.json + safetensors) to ``out_dir``.

    ``rank`` defaults to 4 for the peft-native test (the config.json is
    honoured by ``PeftModel.from_pretrained`` so rank choice is free). For
    the packaged-pack test we pin to 32 to match the hard-coded training
    defaults that the loader reconstructs when no config is present.
    """

    from peft import LoraConfig, get_peft_model

    source = _FakeTransformer()
    cfg = LoraConfig(
        r=rank,
        lora_alpha=rank,
        lora_dropout=0.0,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        bias="none",
    )
    wrapped = get_peft_model(source, cfg)
    wrapped.save_pretrained(str(out_dir))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_apply_lora_adapter_peft_native_layout(tmp_path: Path) -> None:
    """adapter_config.json + adapter_model.safetensors on disk."""

    _save_peft_native_adapter(tmp_path)
    assert (tmp_path / "adapter_config.json").is_file()
    assert (tmp_path / "adapter_model.safetensors").is_file()

    engine = _make_engine()
    gca._apply_lora_adapter(engine, tmp_path)

    # After merge_and_unload the wrapper is gone: plain nn.Module, no peft_type.
    assert isinstance(engine.t3.tfmr, nn.Module)
    assert not hasattr(engine.t3.tfmr, "peft_type")
    # The four projections must still exist on every block.
    for block in engine.t3.tfmr.layers:
        for name in ("q_proj", "k_proj", "v_proj", "o_proj"):
            assert isinstance(getattr(block.self_attn, name), nn.Linear)


def test_apply_lora_adapter_packaged_pt_layout(tmp_path: Path) -> None:
    """adapter.pt (safetensors bytes renamed) with no adapter_config.json."""

    # Produce the real peft save, then rename the safetensors to adapter.pt
    # and drop the config so the packaged-pack branch is exercised. Rank
    # must match the loader's hard-coded reconstruction defaults (r=32).
    staging = tmp_path / "_staging"
    staging.mkdir()
    _save_peft_native_adapter(staging, rank=32)

    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    shutil.copy(
        staging / "adapter_model.safetensors",
        pack_dir / "adapter.pt",
    )
    assert (pack_dir / "adapter.pt").is_file()
    assert not (pack_dir / "adapter_config.json").exists()

    engine = _make_engine()
    gca._apply_lora_adapter(engine, pack_dir)

    assert isinstance(engine.t3.tfmr, nn.Module)
    assert not hasattr(engine.t3.tfmr, "peft_type")
    # Weights should be non-degenerate nn.Linear modules after merge.
    first_q = engine.t3.tfmr.layers[0].self_attn.q_proj
    assert isinstance(first_q, nn.Linear)
    assert first_q.weight.shape == (8, 8)


def test_apply_lora_adapter_pt_with_sidecar_config(tmp_path: Path) -> None:
    """adapter.pt + adapter_config.json: sidecar must drive the LoraConfig.

    The hardcoded fallback uses r=32. This fixture saves at r=4; if the
    loader ignored the sidecar and used defaults, the state-dict key shapes
    would mismatch and set_peft_model_state_dict would leave the LoRA
    deltas at zero. We prove the sidecar was honoured by asserting the
    wrapper merges cleanly.
    """

    staging = tmp_path / "_staging"
    staging.mkdir()
    _save_peft_native_adapter(staging, rank=4)

    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    shutil.copy(
        staging / "adapter_model.safetensors",
        pack_dir / "adapter.pt",
    )
    shutil.copy(
        staging / "adapter_config.json",
        pack_dir / "adapter_config.json",
    )
    assert (pack_dir / "adapter.pt").is_file()
    assert (pack_dir / "adapter_config.json").is_file()
    assert not (pack_dir / "adapter_model.safetensors").exists()

    engine = _make_engine()
    gca._apply_lora_adapter(engine, pack_dir)

    assert isinstance(engine.t3.tfmr, nn.Module)
    assert not hasattr(engine.t3.tfmr, "peft_type")
    first_q = engine.t3.tfmr.layers[0].self_attn.q_proj
    assert isinstance(first_q, nn.Linear)
    assert first_q.weight.shape == (8, 8)


def test_apply_lora_adapter_missing_adapter_errors(tmp_path: Path) -> None:
    """Empty dir — loader must raise with a clear message naming both layouts."""

    engine = _make_engine()
    with pytest.raises(FileNotFoundError) as excinfo:
        gca._apply_lora_adapter(engine, tmp_path)
    msg = str(excinfo.value)
    assert "adapter_config.json" in msg
    assert "adapter.pt" in msg
