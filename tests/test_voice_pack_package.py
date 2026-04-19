"""Tests for ``scripts/voice_pack_package.py``.

The CLI is loaded via ``importlib`` because ``scripts/`` is not a Python
package; that mirrors how the other voice-pack-stage tests in this repo
reach their sibling scripts.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

# --- load the CLI module directly from scripts/ ------------------------------
_spec = importlib.util.spec_from_file_location(
    "voice_pack_package",
    Path(__file__).resolve().parents[1] / "scripts" / "voice_pack_package.py",
)
voice_pack_package = importlib.util.module_from_spec(_spec)
sys.modules["voice_pack_package"] = voice_pack_package
assert _spec.loader is not None
_spec.loader.exec_module(voice_pack_package)

from src.voice_pack.pack import (  # noqa: E402
    VOICE_PACK_FORMAT_VERSION,
    VoicePack,
    load_pack,
)


# --- helpers ----------------------------------------------------------------
def _make_file(path: Path, payload: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _common_kwargs(
    tmp_path: Path,
    *,
    tier: str,
    with_adapter: bool = False,
    with_reference: bool = False,
) -> dict:
    sample = _make_file(tmp_path / "in" / "sample.wav", b"WAVE")
    kwargs: dict = {
        "out_dir": tmp_path / "out",
        "name": "Test Voice",
        "language": "en",
        "tier": tier,
        "tier_reason": "because",
        "total_source_minutes": 12.5,
        "sample_path": sample,
    }
    if with_adapter:
        kwargs["adapter_path"] = _make_file(tmp_path / "in" / "adapter.pt", b"LORA")
    if with_reference:
        kwargs["reference_path"] = _make_file(tmp_path / "in" / "reference.wav", b"REF")
    return kwargs


# --- unit tests -------------------------------------------------------------
def test_build_meta_defaults() -> None:
    meta = voice_pack_package.build_meta(
        name="N",
        language="en",
        tier="full_lora",
        tier_reason="plenty of data",
        total_source_minutes=300.0,
        now_iso="2026-04-17T00:00:00+00:00",
    )
    assert meta.name == "N"
    assert meta.language == "en"
    assert meta.tier == "full_lora"
    assert meta.tier_reason == "plenty of data"
    assert meta.total_source_minutes == 300.0
    assert meta.emotion_coverage == {}
    assert meta.base_model == "chatterbox-multilingual"
    assert meta.format_version == VOICE_PACK_FORMAT_VERSION
    assert meta.created_at == "2026-04-17T00:00:00+00:00"
    assert meta.notes == ""


def test_package_full_lora_creates_files(tmp_path: Path) -> None:
    kwargs = _common_kwargs(tmp_path, tier="full_lora", with_adapter=True)
    result = voice_pack_package.package(**kwargs)

    assert result.is_dir()
    assert (result / "meta.yaml").is_file()
    assert (result / "sample.wav").is_file()
    assert (result / "adapter.pt").is_file()
    assert not (result / "reference.wav").exists()


def test_package_few_shot_creates_files(tmp_path: Path) -> None:
    kwargs = _common_kwargs(tmp_path, tier="few_shot", with_reference=True)
    result = voice_pack_package.package(**kwargs)

    assert (result / "meta.yaml").is_file()
    assert (result / "sample.wav").is_file()
    assert (result / "reference.wav").is_file()
    assert not (result / "adapter.pt").exists()


def test_package_full_lora_missing_adapter_raises(tmp_path: Path) -> None:
    kwargs = _common_kwargs(tmp_path, tier="full_lora", with_adapter=False)
    with pytest.raises(ValueError) as exc:
        voice_pack_package.package(**kwargs)
    assert "adapter" in str(exc.value).lower()


def test_package_few_shot_missing_reference_raises(tmp_path: Path) -> None:
    kwargs = _common_kwargs(tmp_path, tier="few_shot", with_reference=False)
    with pytest.raises(ValueError) as exc:
        voice_pack_package.package(**kwargs)
    assert "reference" in str(exc.value).lower()


def test_package_reduced_lora_accepts_adapter(tmp_path: Path) -> None:
    kwargs = _common_kwargs(tmp_path, tier="reduced_lora", with_adapter=True)
    result = voice_pack_package.package(**kwargs)
    assert (result / "adapter.pt").is_file()


def test_package_accepts_peft_adapter_directory(tmp_path: Path) -> None:
    """A PEFT save-directory should be accepted as ``--adapter``.

    ``peft.PeftModel.save_pretrained`` writes a folder containing
    ``adapter_model.safetensors`` + ``adapter_config.json``. Pointing
    ``--adapter`` at that folder used to raise ``FileNotFoundError``
    from ``_require_file``; now the packager auto-resolves the inner
    file.
    """

    adapter_dir = tmp_path / "in" / "adapter"
    adapter_dir.mkdir(parents=True)
    _make_file(adapter_dir / "adapter_model.safetensors", b"LORA")
    _make_file(adapter_dir / "adapter_config.json", b"{}")

    kwargs = _common_kwargs(tmp_path, tier="full_lora", with_adapter=False)
    kwargs["adapter_path"] = adapter_dir
    result = voice_pack_package.package(**kwargs)
    assert (result / "adapter.pt").is_file()
    # Payload copied verbatim from the safetensors file in the dir.
    assert (result / "adapter.pt").read_bytes() == b"LORA"


def test_package_adapter_directory_without_weights_errors(tmp_path: Path) -> None:
    """A directory that lacks ``adapter_model.*`` must fail loudly."""

    empty_dir = tmp_path / "in" / "adapter"
    empty_dir.mkdir(parents=True)
    (empty_dir / "README.md").write_text("nothing useful", encoding="utf-8")

    kwargs = _common_kwargs(tmp_path, tier="full_lora", with_adapter=False)
    kwargs["adapter_path"] = empty_dir
    with pytest.raises(FileNotFoundError) as exc:
        voice_pack_package.package(**kwargs)
    assert "adapter_model" in str(exc.value)


def test_package_unknown_tier_raises(tmp_path: Path) -> None:
    kwargs = _common_kwargs(tmp_path, tier="bogus_tier")
    with pytest.raises(ValueError) as exc:
        voice_pack_package.package(**kwargs)
    assert "tier" in str(exc.value).lower()


def test_package_missing_sample_file_raises(tmp_path: Path) -> None:
    kwargs = _common_kwargs(tmp_path, tier="full_lora", with_adapter=True)
    kwargs["sample_path"] = tmp_path / "does_not_exist.wav"
    with pytest.raises(FileNotFoundError):
        voice_pack_package.package(**kwargs)


def test_package_refuses_overwrite_by_default(tmp_path: Path) -> None:
    kwargs = _common_kwargs(tmp_path, tier="full_lora", with_adapter=True)
    voice_pack_package.package(**kwargs)
    with pytest.raises(FileExistsError):
        voice_pack_package.package(**kwargs)


def test_package_overwrite_flag_succeeds(tmp_path: Path) -> None:
    kwargs = _common_kwargs(tmp_path, tier="full_lora", with_adapter=True)
    first = voice_pack_package.package(**kwargs)
    # Drop a marker file that must disappear on overwrite.
    (first / "stale.txt").write_text("stale", encoding="utf-8")
    kwargs["overwrite"] = True
    second = voice_pack_package.package(**kwargs)
    assert second == first
    assert not (second / "stale.txt").exists()
    assert (second / "meta.yaml").is_file()


def test_package_slug_derivation_from_name(tmp_path: Path) -> None:
    kwargs = _common_kwargs(tmp_path, tier="full_lora", with_adapter=True)
    kwargs["name"] = "Köyhä Äijä!"
    result = voice_pack_package.package(**kwargs)
    slug = result.name
    assert slug == slug.lower()
    # ASCII-folded, no unicode preserved, no special chars.
    assert all(ch.isascii() for ch in slug)
    for ch in slug:
        assert ch.isalnum() or ch in ("_", "-")
    assert slug.strip("_-") != ""


def test_package_explicit_slug_used(tmp_path: Path) -> None:
    kwargs = _common_kwargs(tmp_path, tier="full_lora", with_adapter=True)
    kwargs["slug"] = "my-voice"
    result = voice_pack_package.package(**kwargs)
    assert result.name == "my-voice"


def test_package_meta_yaml_contents_roundtrip(tmp_path: Path) -> None:
    kwargs = _common_kwargs(tmp_path, tier="full_lora", with_adapter=True)
    kwargs["emotion_coverage"] = {"neutral": 10, "angry": 2}
    kwargs["notes"] = "hand-picked"
    kwargs["now_iso"] = "2026-04-17T12:00:00+00:00"
    result = voice_pack_package.package(**kwargs)

    raw = yaml.safe_load((result / "meta.yaml").read_text(encoding="utf-8"))
    assert raw["name"] == "Test Voice"
    assert raw["language"] == "en"
    assert raw["tier"] == "full_lora"
    assert raw["tier_reason"] == "because"
    assert raw["total_source_minutes"] == pytest.approx(12.5)
    assert raw["emotion_coverage"] == {"neutral": 10, "angry": 2}
    assert raw["base_model"] == "chatterbox-multilingual"
    assert raw["format_version"] == VOICE_PACK_FORMAT_VERSION
    assert raw["created_at"] == "2026-04-17T12:00:00+00:00"
    assert raw["notes"] == "hand-picked"


def test_package_loads_via_load_pack(tmp_path: Path) -> None:
    kwargs = _common_kwargs(tmp_path, tier="full_lora", with_adapter=True)
    result = voice_pack_package.package(**kwargs)
    loaded = load_pack(result)
    assert isinstance(loaded, VoicePack)
    assert loaded.meta.name == "Test Voice"
    assert loaded.meta.tier == "full_lora"


# --- CLI (main) tests -------------------------------------------------------
def _main_args(
    tmp_path: Path,
    *,
    tier: str,
    include_adapter: bool = False,
    include_reference: bool = False,
    omit_tier: bool = False,
) -> list[str]:
    sample = _make_file(tmp_path / "in" / "sample.wav", b"WAVE")
    args = [
        "--out", str(tmp_path / "out"),
        "--name", "Test Voice",
        "--language", "en",
        "--tier-reason", "because",
        "--total-source-minutes", "5.0",
        "--sample", str(sample),
    ]
    if not omit_tier:
        args += ["--tier", tier]
    if include_adapter:
        args += [
            "--adapter",
            str(_make_file(tmp_path / "in" / "adapter.pt", b"LORA")),
        ]
    if include_reference:
        args += [
            "--reference",
            str(_make_file(tmp_path / "in" / "reference.wav", b"REF")),
        ]
    return args


def test_main_happy_path(tmp_path: Path) -> None:
    argv = _main_args(tmp_path, tier="full_lora", include_adapter=True)
    rc = voice_pack_package.main(argv)
    assert rc == 0
    # Slug derives from "Test Voice" -> "test_voice".
    out_root = tmp_path / "out"
    children = [p for p in out_root.iterdir() if p.is_dir()]
    assert len(children) == 1
    assert (children[0] / "meta.yaml").is_file()


def test_main_missing_required_arg_exits_nonzero(tmp_path: Path) -> None:
    argv = _main_args(tmp_path, tier="full_lora", include_adapter=True, omit_tier=True)
    with pytest.raises(SystemExit) as exc:
        voice_pack_package.main(argv)
    # argparse exits with non-zero status on missing required args.
    assert exc.value.code != 0


def test_main_emotion_coverage_parsed(tmp_path: Path) -> None:
    argv = _main_args(tmp_path, tier="full_lora", include_adapter=True)
    argv += ["--emotion-coverage", "neutral=10,angry=2"]
    rc = voice_pack_package.main(argv)
    assert rc == 0

    children = [p for p in (tmp_path / "out").iterdir() if p.is_dir()]
    assert len(children) == 1
    meta = yaml.safe_load((children[0] / "meta.yaml").read_text(encoding="utf-8"))
    assert meta["emotion_coverage"] == {"neutral": 10, "angry": 2}
    assert all(isinstance(v, int) for v in meta["emotion_coverage"].values())


def test_main_emotion_coverage_invalid_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = _main_args(tmp_path, tier="full_lora", include_adapter=True)
    argv += ["--emotion-coverage", "bad_format"]
    rc = voice_pack_package.main(argv)
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.err.strip() != ""
    # Single-line error.
    assert captured.err.strip().count("\n") == 0


def test_main_tier_mismatch_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # full_lora without --adapter must fail cleanly through main().
    argv = _main_args(tmp_path, tier="full_lora", include_adapter=False)
    rc = voice_pack_package.main(argv)
    assert rc == 1
    captured = capsys.readouterr()
    assert "adapter" in captured.err.lower()
    assert captured.err.strip().count("\n") == 0
