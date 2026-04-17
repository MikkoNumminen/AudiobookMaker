"""Tests for scripts/voice_pack_train.py.

These tests must not require torch, peft, Chatterbox, or a GPU. They
exercise the config dataclass, tier policy, manifest loader, run-directory
preparation, and CLI dry-run / error paths. The actual training loop is a
seam (:func:`_run_training`) that raises :class:`NotImplementedError` and
is covered only at that boundary.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# scripts/ isn't a Python package, so load voice_pack_train.py by path.
_spec = importlib.util.spec_from_file_location(
    "voice_pack_train",
    Path(__file__).resolve().parents[1] / "scripts" / "voice_pack_train.py",
)
voice_pack_train = importlib.util.module_from_spec(_spec)
sys.modules["voice_pack_train"] = voice_pack_train
assert _spec.loader is not None
_spec.loader.exec_module(voice_pack_train)

# Also make src/ importable so we can build manifests in tests.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.voice_pack.types import DatasetClip, DatasetManifest  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_manifest(
    tmp_path: Path,
    *,
    total_seconds: float,
    speaker: str = "SPEAKER_00",
    n_clips: int = 2,
) -> Path:
    """Serialise a minimal DatasetManifest to tmp_path/manifest.json."""
    clips = [
        DatasetClip(
            path=f"clip_{i:03d}.wav",
            text=f"Example sentence number {i}.",
            emotion="neutral",
            speaker=speaker,
            duration=total_seconds / max(1, n_clips),
        )
        for i in range(n_clips)
    ]
    manifest = DatasetManifest(
        speaker=speaker,
        root_dir=tmp_path,
        clips=clips,
        total_seconds=total_seconds,
        emotion_counts={"neutral": n_clips},
        sample_rate_hz=24000,
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), indent=2), encoding="utf-8"
    )
    return manifest_path


def _make_config(
    manifest_path: Path, out_dir: Path
) -> "voice_pack_train.TrainConfig":
    return voice_pack_train.TrainConfig(
        manifest_path=manifest_path, out_dir=out_dir
    )


# ---------------------------------------------------------------------------
# TrainConfig
# ---------------------------------------------------------------------------


def test_train_config_defaults_roundtrip(tmp_path: Path) -> None:
    config = voice_pack_train.TrainConfig(
        manifest_path=tmp_path / "manifest.json",
        out_dir=tmp_path / "out",
    )
    data = config.to_dict()

    expected_keys = {
        "manifest_path",
        "out_dir",
        "base_model",
        "lora_rank",
        "lora_alpha",
        "lora_dropout",
        "learning_rate",
        "batch_size",
        "grad_accum_steps",
        "epochs",
        "max_steps",
        "warmup_ratio",
        "weight_decay",
        "mixed_precision",
        "early_stopping_patience",
        "seed",
        "save_every_n_steps",
        "eval_every_n_steps",
        "reduced_mode",
    }
    assert expected_keys.issubset(data.keys())

    # Paths must stringify, and the dict must round-trip through json.
    assert isinstance(data["manifest_path"], str)
    assert isinstance(data["out_dir"], str)
    serialised = json.dumps(data)
    reparsed = json.loads(serialised)
    assert reparsed["base_model"] == "chatterbox-multilingual"
    assert reparsed["lora_rank"] == 16
    assert reparsed["mixed_precision"] == "fp16"


# ---------------------------------------------------------------------------
# apply_tier_policy
# ---------------------------------------------------------------------------


def test_apply_tier_policy_full(tmp_path: Path) -> None:
    config = _make_config(tmp_path / "m.json", tmp_path / "out")
    updated = voice_pack_train.apply_tier_policy(config, 40 * 60)
    # Default 16 bumps to 32 at full tier.
    assert updated.lora_rank in (16, 32)
    assert updated.lora_rank >= 16
    assert updated.reduced_mode is False


def test_apply_tier_policy_reduced(tmp_path: Path) -> None:
    config = _make_config(tmp_path / "m.json", tmp_path / "out")
    updated = voice_pack_train.apply_tier_policy(config, 15 * 60)
    assert updated.lora_rank == 8
    assert updated.lora_alpha == 16
    assert updated.epochs == 2
    assert updated.lora_dropout == pytest.approx(0.1)
    assert updated.early_stopping_patience == 2
    assert updated.reduced_mode is True


def test_apply_tier_policy_few_shot_raises(tmp_path: Path) -> None:
    config = _make_config(tmp_path / "m.json", tmp_path / "out")
    with pytest.raises(ValueError) as excinfo:
        voice_pack_train.apply_tier_policy(config, 5 * 60)
    msg = str(excinfo.value)
    assert "few_shot" in msg
    assert "few-shot" in msg


def test_apply_tier_policy_skip_raises(tmp_path: Path) -> None:
    config = _make_config(tmp_path / "m.json", tmp_path / "out")
    with pytest.raises(ValueError):
        voice_pack_train.apply_tier_policy(config, 30.0)


# ---------------------------------------------------------------------------
# load_manifest
# ---------------------------------------------------------------------------


def test_load_manifest_roundtrip(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path, total_seconds=42 * 60, speaker="SPEAKER_42", n_clips=2
    )
    loaded = voice_pack_train.load_manifest(manifest_path)

    assert loaded.speaker == "SPEAKER_42"
    assert loaded.total_seconds == pytest.approx(42 * 60)
    assert len(loaded.clips) == 2
    first = loaded.clips[0]
    assert isinstance(first, DatasetClip)
    assert first.path == "clip_000.wav"
    assert first.emotion == "neutral"


# ---------------------------------------------------------------------------
# prepare_training_run
# ---------------------------------------------------------------------------


def test_prepare_training_run_writes_files(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, total_seconds=40 * 60)
    out_dir = tmp_path / "run_out"
    config = _make_config(manifest_path, out_dir)

    returned = voice_pack_train.prepare_training_run(config)

    assert returned == out_dir
    config_json = out_dir / "config.json"
    manifest_snap = out_dir / "manifest_snapshot.json"
    run_cmd = out_dir / "run_command.txt"
    for path in (config_json, manifest_snap, run_cmd):
        assert path.exists(), f"expected {path} to be written"

    # Config and manifest snapshot must parse as JSON.
    json.loads(config_json.read_text(encoding="utf-8"))
    snap = json.loads(manifest_snap.read_text(encoding="utf-8"))
    assert snap["speaker"] == "SPEAKER_00"

    # run_command.txt should be a reproducible argv command.
    cmd_text = run_cmd.read_text(encoding="utf-8")
    assert "voice_pack_train.py" in cmd_text
    assert "--manifest" in cmd_text
    assert "--out" in cmd_text


def test_prepare_training_run_creates_out_dir(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, total_seconds=40 * 60)
    out_dir = tmp_path / "does_not_exist_yet" / "run"
    assert not out_dir.exists()

    config = _make_config(manifest_path, out_dir)
    voice_pack_train.prepare_training_run(config)

    assert out_dir.exists()
    assert out_dir.is_dir()


# ---------------------------------------------------------------------------
# _run_training seam
# ---------------------------------------------------------------------------


def test_run_training_raises_not_implemented(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, total_seconds=40 * 60)
    manifest = voice_pack_train.load_manifest(manifest_path)
    config = _make_config(manifest_path, tmp_path / "out")

    with pytest.raises(NotImplementedError) as excinfo:
        voice_pack_train._run_training(config, manifest)
    msg = str(excinfo.value)
    assert "GPU" in msg or "implementation" in msg.lower()


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------


def test_main_dry_run_returns_zero(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    manifest_path = _write_manifest(tmp_path, total_seconds=40 * 60)
    out_dir = tmp_path / "run"

    rc = voice_pack_train.main(
        [
            "--manifest",
            str(manifest_path),
            "--out",
            str(out_dir),
            "--dry-run",
        ]
    )
    assert rc == 0
    captured = capfd.readouterr()
    assert "Dry run complete" in captured.out
    assert out_dir.exists()


def test_main_without_dry_run_returns_one_and_reports_not_implemented(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _write_manifest(tmp_path, total_seconds=40 * 60)
    out_dir = tmp_path / "run"

    rc = voice_pack_train.main(
        ["--manifest", str(manifest_path), "--out", str(out_dir)]
    )
    assert rc == 1
    captured = capfd.readouterr()
    stderr_lower = captured.err.lower()
    assert "gpu" in stderr_lower or "not implemented" in stderr_lower


def test_main_missing_manifest_returns_one(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.json"
    rc = voice_pack_train.main(
        [
            "--manifest",
            str(missing),
            "--out",
            str(tmp_path / "run"),
            "--dry-run",
        ]
    )
    assert rc == 1
    captured = capfd.readouterr()
    assert "manifest" in captured.err.lower()
    assert "not found" in captured.err.lower() or "nope.json" in captured.err


def test_main_few_shot_tier_returns_one_with_reason(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _write_manifest(tmp_path, total_seconds=2 * 60)
    rc = voice_pack_train.main(
        [
            "--manifest",
            str(manifest_path),
            "--out",
            str(tmp_path / "run"),
            "--dry-run",
        ]
    )
    assert rc == 1
    captured = capfd.readouterr()
    err_lower = captured.err.lower()
    assert "few_shot" in err_lower or "few-shot" in err_lower
    assert "tier" in err_lower or "does not support" in err_lower
