"""Tests for the voice-pack on-disk artefact format (``voice_pack.pack``)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.voice_pack.pack import (
    PACK_ARCHIVE_SUFFIX,
    VOICE_PACK_FORMAT_VERSION,
    VoicePack,
    VoicePackError,
    VoicePackMeta,
    _extract_pack_archive,
    base_model_requirements,
    default_voice_packs_root,
    export_pack,
    install_pack,
    install_pack_source,
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


# ---------------------------------------------------------------------------
# base_model_requirements
# ---------------------------------------------------------------------------


def test_base_model_requirements_non_finnish_lists_base_only() -> None:
    meta = VoicePackMeta(
        name="X", language="en", tier="few_shot",
        tier_reason="r", total_source_minutes=1.0,
    )
    reqs = base_model_requirements(meta)
    assert reqs == ["Chatterbox multilingual base model"]


def test_base_model_requirements_finnish_adds_finetune() -> None:
    meta = VoicePackMeta(
        name="X", language="fi", tier="few_shot",
        tier_reason="r", total_source_minutes=1.0,
    )
    reqs = base_model_requirements(meta)
    assert len(reqs) == 2
    assert any("Finnish" in r for r in reqs)


def test_base_model_requirements_finnish_case_and_region_insensitive() -> None:
    # "FI", "fi-FI" and leading/trailing space all count as Finnish.
    for lang in ("FI", "fi-FI", " fi "):
        meta = VoicePackMeta(
            name="X", language=lang, tier="few_shot",
            tier_reason="r", total_source_minutes=1.0,
        )
        assert len(base_model_requirements(meta)) == 2, lang


# ---------------------------------------------------------------------------
# export_pack / install_pack_source round-trip
# ---------------------------------------------------------------------------


def test_export_pack_default_name_and_layout(tmp_path: Path, monkeypatch) -> None:
    source = _make_few_shot_pack(tmp_path / "src", name="Granny")
    # Default out path lands in the CWD; chdir into tmp so we don't litter.
    monkeypatch.chdir(tmp_path)

    out = export_pack(source)
    assert out.name == f"{source.name}{PACK_ARCHIVE_SUFFIX}"
    assert out.exists()

    import zipfile

    with zipfile.ZipFile(out) as zf:
        members = sorted(zf.namelist())
    # Every member is nested under a single top-level <slug>/ directory.
    assert members == [
        f"{source.name}/meta.yaml",
        f"{source.name}/reference.wav",
        f"{source.name}/sample.wav",
    ]


def test_export_pack_explicit_out_creates_parent_dirs(tmp_path: Path) -> None:
    source = _make_few_shot_pack(tmp_path / "src", name="Granny")
    out = tmp_path / "nested" / "dir" / "voice.zip"

    written = export_pack(source, out)
    assert written == out
    assert out.exists()


def test_export_pack_rejects_invalid_pack(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus"
    bogus.mkdir()
    (bogus / "stray.txt").write_text("not a pack", encoding="utf-8")

    with pytest.raises(VoicePackError):
        export_pack(bogus, tmp_path / "out.zip")


def test_export_then_install_round_trip(tmp_path: Path) -> None:
    source = _make_few_shot_pack(tmp_path / "src", name="Round Trip")
    archive = export_pack(source, tmp_path / "rt.abvpack.zip")

    dest_root = tmp_path / "installed"
    pack = install_pack_source(archive, dest_root)

    assert pack.meta.name == "Round Trip"
    assert pack.root.parent == dest_root
    # Reloading from disk confirms a complete, valid pack landed.
    assert load_pack(pack.root).meta.name == "Round Trip"
    assert (pack.root / "reference.wav").exists()


def test_install_pack_source_accepts_directory(tmp_path: Path) -> None:
    source = _make_few_shot_pack(tmp_path / "src", name="Dir Source")
    dest_root = tmp_path / "installed"

    pack = install_pack_source(source, dest_root)
    assert pack.meta.name == "Dir Source"


def test_install_pack_source_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(VoicePackError, match="not found"):
        install_pack_source(tmp_path / "nope.zip", tmp_path / "installed")


def test_install_pack_source_non_zip_file_raises(tmp_path: Path) -> None:
    bad = tmp_path / "notes.txt"
    bad.write_text("hello", encoding="utf-8")
    with pytest.raises(VoicePackError, match="unsupported"):
        install_pack_source(bad, tmp_path / "installed")


def test_install_pack_source_honours_overwrite(tmp_path: Path) -> None:
    source = _make_few_shot_pack(tmp_path / "src", name="Twice")
    archive = export_pack(source, tmp_path / "twice.zip")
    dest_root = tmp_path / "installed"

    install_pack_source(archive, dest_root)
    # Re-importing the same pack collides on the slug.
    with pytest.raises(FileExistsError):
        install_pack_source(archive, dest_root)
    # overwrite=True replaces it cleanly.
    pack = install_pack_source(archive, dest_root, overwrite=True)
    assert pack.meta.name == "Twice"


# ---------------------------------------------------------------------------
# _extract_pack_archive — zip-slip protection
# ---------------------------------------------------------------------------


def test_extract_pack_archive_rejects_parent_escape(tmp_path: Path) -> None:
    import zipfile

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escape.txt", "nope")
        zf.writestr("pack/meta.yaml", "x")

    with pytest.raises(VoicePackError, match="unsafe path"):
        _extract_pack_archive(evil, tmp_path / "dest")
    # Nothing escaped to the parent.
    assert not (tmp_path / "escape.txt").exists()


def test_extract_pack_archive_rejects_absolute_member(tmp_path: Path) -> None:
    import zipfile

    evil = tmp_path / "abs.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        # A leading-slash member name attempts an absolute write.
        zf.writestr("/abs_escape.txt", "nope")

    with pytest.raises(VoicePackError, match="unsafe path"):
        _extract_pack_archive(evil, tmp_path / "dest")


def test_extract_pack_archive_bad_zip_raises(tmp_path: Path) -> None:
    notzip = tmp_path / "broken.zip"
    notzip.write_bytes(b"this is not a zip file")

    with pytest.raises(VoicePackError, match="not a valid zip"):
        _extract_pack_archive(notzip, tmp_path / "dest")


def test_extract_pack_archive_no_meta_raises(tmp_path: Path) -> None:
    import zipfile

    nometa = tmp_path / "nometa.zip"
    with zipfile.ZipFile(nometa, "w") as zf:
        zf.writestr("pack/sample.wav", "x")

    with pytest.raises(VoicePackError, match="no meta.yaml"):
        _extract_pack_archive(nometa, tmp_path / "dest")
