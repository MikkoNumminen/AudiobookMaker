"""Tests for the voice-pack on-disk artefact format (``voice_pack.pack``)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.voice_pack.pack import (
    VOICE_PACK_FORMAT_VERSION,
    VoicePack,
    VoicePackError,
    VoicePackMeta,
    default_voice_packs_root,
    install_pack,
    list_packs,
    load_pack,
    validate_pack_dir,
)


def _write_meta(pack_dir: Path, **overrides: object) -> dict:
    """Write a meta.yaml into ``pack_dir`` and return the dict written."""

    data: dict = {
        "name": "Test Voice",
        "language": "en",
        "tier": "full_lora",
        "tier_reason": "342.7 min clean source",
        "total_source_minutes": 342.7,
        "emotion_coverage": {"neutral": 5820, "angry": 203},
        "base_model": "chatterbox-multilingual",
        "format_version": VOICE_PACK_FORMAT_VERSION,
        "created_at": "2026-04-17T01:28:13+00:00",
        "notes": "",
    }
    data.update(overrides)
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "meta.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return data


def _touch(path: Path, content: bytes = b"\x00") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _make_full_lora_pack(root: Path, name: str = "Test Voice") -> Path:
    pack_dir = root / "pack"
    _write_meta(pack_dir, name=name, tier="full_lora")
    _touch(pack_dir / "sample.wav")
    _touch(pack_dir / "adapter.pt")
    return pack_dir


def _make_few_shot_pack(root: Path, name: str = "Few Shot Voice") -> Path:
    pack_dir = root / "pack"
    _write_meta(
        pack_dir,
        name=name,
        tier="few_shot",
        tier_reason="3.2 min — few-shot fallback",
        total_source_minutes=3.2,
    )
    _touch(pack_dir / "sample.wav")
    _touch(pack_dir / "reference.wav")
    return pack_dir


# ---------------------------------------------------------------------------
# VoicePackMeta
# ---------------------------------------------------------------------------


def test_meta_roundtrip() -> None:
    meta = VoicePackMeta(
        name="Roundtrip",
        language="fi",
        tier="full_lora",
        tier_reason="enough data",
        total_source_minutes=120.5,
        emotion_coverage={"neutral": 100, "happy": 5},
        created_at="2026-04-17T00:00:00+00:00",
        notes="hi",
    )
    again = VoicePackMeta.from_dict(meta.to_dict())
    assert again == meta


def test_meta_from_dict_missing_required() -> None:
    data = {
        "name": "X",
        "language": "en",
        # tier missing
        "tier_reason": "why",
        "total_source_minutes": 1.0,
    }
    with pytest.raises(KeyError) as exc:
        VoicePackMeta.from_dict(data)
    assert "tier" in str(exc.value)


def test_meta_from_dict_unknown_optional_ignored() -> None:
    data = {
        "name": "X",
        "language": "en",
        "tier": "full_lora",
        "tier_reason": "why",
        "total_source_minutes": 1.0,
        "surprise_field": "ignore me",
    }
    meta = VoicePackMeta.from_dict(data)
    assert meta.name == "X"


# ---------------------------------------------------------------------------
# load_pack
# ---------------------------------------------------------------------------


def test_load_pack_full_lora_happy_path(tmp_path: Path) -> None:
    pack_dir = _make_full_lora_pack(tmp_path, name="Happy Voice")
    pack = load_pack(pack_dir)
    assert isinstance(pack, VoicePack)
    assert pack.root == pack_dir
    assert pack.meta.name == "Happy Voice"
    assert pack.meta.tier == "full_lora"
    assert pack.sample_path == pack_dir / "sample.wav"
    assert pack.adapter_path == pack_dir / "adapter.pt"
    assert pack.reference_path == pack_dir / "reference.wav"
    assert pack.display_name == "Happy Voice"


def test_load_pack_few_shot_happy_path(tmp_path: Path) -> None:
    pack_dir = _make_few_shot_pack(tmp_path)
    pack = load_pack(pack_dir)
    assert pack.meta.tier == "few_shot"
    assert not (pack_dir / "adapter.pt").exists()


def test_load_pack_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_pack(tmp_path / "nope")


def test_load_pack_missing_meta(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    _touch(pack_dir / "sample.wav")
    with pytest.raises(VoicePackError):
        load_pack(pack_dir)


def test_load_pack_bad_yaml(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "meta.yaml").write_text(
        "name: unterminated\nlist: [this is : : broken", encoding="utf-8"
    )
    _touch(pack_dir / "sample.wav")
    with pytest.raises(VoicePackError):
        load_pack(pack_dir)


def test_load_pack_unknown_tier(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    _write_meta(pack_dir, tier="bogus")
    _touch(pack_dir / "sample.wav")
    with pytest.raises(VoicePackError) as exc:
        load_pack(pack_dir)
    assert "tier" in str(exc.value)


def test_load_pack_future_format_version(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    _write_meta(pack_dir, format_version=VOICE_PACK_FORMAT_VERSION + 7)
    _touch(pack_dir / "sample.wav")
    _touch(pack_dir / "adapter.pt")
    with pytest.raises(VoicePackError) as exc:
        load_pack(pack_dir)
    assert "newer" in str(exc.value)


def test_load_pack_missing_tier_asset(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    _write_meta(pack_dir, tier="full_lora")
    _touch(pack_dir / "sample.wav")
    # adapter.pt intentionally missing
    with pytest.raises(VoicePackError) as exc:
        load_pack(pack_dir)
    assert "adapter" in str(exc.value)


def test_load_pack_missing_sample_wav(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    _write_meta(pack_dir, tier="full_lora")
    _touch(pack_dir / "adapter.pt")
    # sample.wav intentionally missing
    with pytest.raises(VoicePackError) as exc:
        load_pack(pack_dir)
    assert "sample" in str(exc.value)


# ---------------------------------------------------------------------------
# validate_pack_dir
# ---------------------------------------------------------------------------


def test_validate_pack_dir_reports_every_issue(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    # no meta.yaml, no sample.wav
    issues = validate_pack_dir(pack_dir)
    joined = " | ".join(issues)
    assert "meta.yaml missing" in joined
    assert "sample.wav missing" in joined


def test_validate_pack_dir_empty_on_valid(tmp_path: Path) -> None:
    pack_dir = _make_full_lora_pack(tmp_path)
    assert validate_pack_dir(pack_dir) == []


# ---------------------------------------------------------------------------
# list_packs
# ---------------------------------------------------------------------------


def test_list_packs_scans_directory(tmp_path: Path) -> None:
    root = tmp_path / "packs_root"
    root.mkdir()

    for display in ("Bravo Voice", "Alpha Voice", "Charlie Voice"):
        pack_dir = root / display.lower().replace(" ", "_")
        _write_meta(pack_dir, name=display, tier="full_lora")
        _touch(pack_dir / "sample.wav")
        _touch(pack_dir / "adapter.pt")

    # One broken pack — missing adapter.pt.
    broken = root / "broken"
    _write_meta(broken, name="Broken Voice", tier="full_lora")
    _touch(broken / "sample.wav")

    packs = list_packs(root)
    assert [p.meta.name for p in packs] == ["Alpha Voice", "Bravo Voice", "Charlie Voice"]


def test_list_packs_nonexistent_root(tmp_path: Path) -> None:
    assert list_packs(tmp_path / "does-not-exist") == []


# ---------------------------------------------------------------------------
# install_pack
# ---------------------------------------------------------------------------


def test_install_pack_copies_and_returns(tmp_path: Path) -> None:
    source = _make_full_lora_pack(tmp_path / "source_parent", name="Install Me")
    dest_root = tmp_path / "dest_root"

    pack = install_pack(source, dest_root)

    assert isinstance(pack, VoicePack)
    assert pack.root.parent == dest_root
    assert pack.root.exists()
    assert (pack.root / "meta.yaml").exists()
    assert (pack.root / "sample.wav").exists()
    assert (pack.root / "adapter.pt").exists()


def test_install_pack_refuses_overwrite_by_default(tmp_path: Path) -> None:
    source = _make_full_lora_pack(tmp_path / "src", name="Clasher")
    dest_root = tmp_path / "dest"

    install_pack(source, dest_root)
    with pytest.raises(FileExistsError):
        install_pack(source, dest_root)


def test_install_pack_overwrites_when_flag_set(tmp_path: Path) -> None:
    source = _make_full_lora_pack(tmp_path / "src", name="Clasher")
    dest_root = tmp_path / "dest"

    install_pack(source, dest_root)
    pack = install_pack(source, dest_root, overwrite=True)
    assert pack.root.exists()


def test_install_pack_rename_to_slug_used_as_folder(tmp_path: Path) -> None:
    source = _make_full_lora_pack(tmp_path / "src", name="Anything")
    dest_root = tmp_path / "dest"

    pack = install_pack(source, dest_root, rename_to="my-voice")
    assert pack.root.name == "my-voice"


def test_install_pack_slug_from_name(tmp_path: Path) -> None:
    source = _make_full_lora_pack(tmp_path / "src", name="Käärmeen Ääni!")
    dest_root = tmp_path / "dest"

    pack = install_pack(source, dest_root)
    slug = pack.root.name
    assert slug == slug.lower()
    # Only a-z, 0-9, _ or - allowed.
    assert all(ch.isalnum() and ch.isascii() or ch in "_-" for ch in slug)
    assert slug  # non-empty


# ---------------------------------------------------------------------------
# default_voice_packs_root
# ---------------------------------------------------------------------------


def test_default_voice_packs_root() -> None:
    assert default_voice_packs_root() == Path.home() / ".audiobookmaker" / "voice_packs"
